import httpx
import pytest

from app import whisper_client


class FakeResponse:
    def __init__(self, status_code: int, text: str = ""):
        self.status_code = status_code
        self.text = text


class FakeClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append({
            "url": url,
            "headers": kwargs.get("headers", {}),
            "data": kwargs.get("data", {}),
        })
        if not self._responses:
            raise AssertionError("extra call")
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def close(self):
        pass


SRT_OK = "1\n00:00:00,000 --> 00:00:01,000\nпривет\n"


def make_audio(tmp_path):
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"x")
    return audio


def test_success_first_try(tmp_path, monkeypatch):
    audio = make_audio(tmp_path)
    client = FakeClient([FakeResponse(200, SRT_OK)])
    sleeps = []
    monkeypatch.setattr(whisper_client.time, "sleep", lambda s: sleeps.append(s))

    out = whisper_client.transcribe_srt(audio, "sk-test", language="ru", client=client)

    assert out == SRT_OK
    assert sleeps == []
    assert len(client.calls) == 1
    assert client.calls[0]["url"] == whisper_client.OPENAI_TRANSCRIPTIONS_URL
    assert client.calls[0]["headers"]["Authorization"] == "Bearer sk-test"
    assert client.calls[0]["data"]["model"] == "whisper-1"
    assert client.calls[0]["data"]["response_format"] == "srt"
    assert client.calls[0]["data"]["language"] == "ru"


def test_retries_then_fails(tmp_path, monkeypatch):
    audio = make_audio(tmp_path)
    client = FakeClient([
        FakeResponse(500, "boom"),
        httpx.ConnectError("net down"),
        FakeResponse(429, "rate"),
    ])
    sleeps = []
    monkeypatch.setattr(whisper_client.time, "sleep", lambda s: sleeps.append(s))

    with pytest.raises(whisper_client.WhisperError):
        whisper_client.transcribe_srt(audio, "sk-test", client=client)

    assert sleeps == [1, 3]
    assert len(client.calls) == 3


def test_auth_error_no_retry(tmp_path, monkeypatch):
    audio = make_audio(tmp_path)
    client = FakeClient([FakeResponse(401, "bad key")])
    sleeps = []
    monkeypatch.setattr(whisper_client.time, "sleep", lambda s: sleeps.append(s))

    with pytest.raises(whisper_client.WhisperError, match="401"):
        whisper_client.transcribe_srt(audio, "sk-test", client=client)

    assert sleeps == []
    assert len(client.calls) == 1


def test_network_error_is_retried(tmp_path, monkeypatch):
    audio = make_audio(tmp_path)
    client = FakeClient([httpx.ConnectError("net"), FakeResponse(200, SRT_OK)])
    monkeypatch.setattr(whisper_client.time, "sleep", lambda s: None)

    assert whisper_client.transcribe_srt(audio, "sk-test", client=client) == SRT_OK
