import pytest

from app import transcription
from app.vtt import Segment

GOOD_VTT = "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nПривет из субтитров\n"
URL = "https://youtu.be/abc123def45"


def make_info(subs=None, auto=None, title="Test video"):
    return {
        "id": "abc123def45",
        "title": title,
        "subtitles": subs or {},
        "automatic_captions": auto or {},
    }


def vtt_entry(prefix):
    return {
        "ru": [{"ext": "vtt", "url": f"http://cap/{prefix}/ru.vtt"}],
        "en": [{"ext": "vtt", "url": f"http://cap/{prefix}/en.vtt"}],
    }


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(transcription.cache, "DATA_DIR", tmp_path)
    return tmp_path


def test_manual_subtitles_preferred(data_dir, monkeypatch):
    monkeypatch.setattr(
        transcription, "_extract_info",
        lambda url: make_info(subs=vtt_entry("manual"), auto=vtt_entry("auto")))
    monkeypatch.setattr(transcription, "_fetch_captions", lambda u: GOOD_VTT)

    res = transcription.get_transcript(URL, api_key=None)

    assert res.ok
    assert res.source == "manual"
    assert res.language == "ru"
    assert res.title == "Test video"
    assert res.segments[0].text == "Привет из субтитров"
    assert set(res.available_languages) == {"ru", "en"}
    assert (data_dir / "abc123def45.json").exists()


def test_falls_back_to_auto_captions(data_dir, monkeypatch):
    monkeypatch.setattr(
        transcription, "_extract_info",
        lambda url: make_info(subs={}, auto=vtt_entry("auto")))
    monkeypatch.setattr(transcription, "_fetch_captions", lambda u: GOOD_VTT)

    res = transcription.get_transcript(URL, api_key=None)

    assert res.ok
    assert res.source == "auto"


def test_caption_fetch_error_falls_to_next_source(data_dir, monkeypatch):
    monkeypatch.setattr(
        transcription, "_extract_info",
        lambda url: make_info(subs=vtt_entry("manual"), auto=vtt_entry("auto")))

    def fetch(url):
        if url.startswith("http://cap/manual"):
            raise RuntimeError("403 from youtube")
        return GOOD_VTT

    monkeypatch.setattr(transcription, "_fetch_captions", fetch)

    res = transcription.get_transcript(URL, api_key=None)

    assert res.ok
    assert res.source == "auto"


def test_no_subs_without_key(data_dir, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(transcription, "_extract_info", lambda url: make_info())
    called = []
    monkeypatch.setattr(
        transcription, "_transcribe_via_whisper", lambda *a, **k: called.append(1))

    res = transcription.get_transcript(URL, api_key=None)

    assert not res.ok
    assert res.segments == []
    assert "нет транскриптов" in res.error
    assert called == []


def test_whisper_used_when_no_subs(data_dir, monkeypatch):
    monkeypatch.setattr(transcription, "_extract_info", lambda url: make_info())
    monkeypatch.setattr(
        transcription, "_transcribe_via_whisper",
        lambda url, lang, key: ([Segment(0.0, 1.0, "расшифровка")], "ru"))

    res = transcription.get_transcript(URL, api_key="sk-test")

    assert res.ok
    assert res.source == "whisper"
    assert res.segments[0].text == "расшифровка"
    assert transcription.cache.load("abc123def45")["source"] == "whisper"


def test_whisper_failure_reported_as_error(data_dir, monkeypatch):
    monkeypatch.setattr(transcription, "_extract_info", lambda url: make_info())

    def boom(url, lang, key):
        raise RuntimeError("429")

    monkeypatch.setattr(transcription, "_transcribe_via_whisper", boom)

    res = transcription.get_transcript(URL, api_key="sk-test")

    assert not res.ok
    assert "Whisper недоступен" in res.error


def test_extract_info_failure_is_error(data_dir, monkeypatch):
    def boom(url):
        raise RuntimeError("приватное видео")

    monkeypatch.setattr(transcription, "_extract_info", boom)

    res = transcription.get_transcript(URL, api_key=None)

    assert not res.ok
    assert "не удалось получить видео" in res.error
    assert "приватное видео" in res.error


def test_cache_hit_skips_network(data_dir, monkeypatch):
    calls = []

    def extract(url):
        calls.append(url)
        return make_info(subs=vtt_entry("manual"))

    monkeypatch.setattr(transcription, "_extract_info", extract)
    monkeypatch.setattr(transcription, "_fetch_captions", lambda u: GOOD_VTT)

    first = transcription.get_transcript(URL, api_key=None)
    second = transcription.get_transcript(URL, api_key=None)

    assert first.ok and second.ok
    assert len(calls) == 1


def test_stale_cache_entry_is_refetched(data_dir, monkeypatch):
    monkeypatch.setattr(
        transcription, "_extract_info",
        lambda url: make_info(subs=vtt_entry("manual")))
    monkeypatch.setattr(transcription, "_fetch_captions", lambda u: GOOD_VTT)

    transcription.get_transcript(URL, api_key=None)

    stale = transcription.cache.load("abc123def45")
    stale.pop("parser_rev", None)
    transcription.cache.save("abc123def45", stale)

    calls = []

    def extract(url):
        calls.append(url)
        return make_info(subs=vtt_entry("manual"))

    monkeypatch.setattr(transcription, "_extract_info", extract)
    res = transcription.get_transcript(URL, api_key=None)

    assert res.ok
    assert len(calls) == 1
    assert transcription.cache.load("abc123def45").get("parser_rev")


def test_strict_lang_choice_skips_to_auto(data_dir, monkeypatch):
    monkeypatch.setattr(
        transcription, "_extract_info",
        lambda url: make_info(
            subs={"ru": [{"ext": "vtt", "url": "http://cap/manual/ru.vtt"}]},
            auto={"de": [{"ext": "vtt", "url": "http://cap/auto/de.vtt"}]},
        ))
    monkeypatch.setattr(transcription, "_fetch_captions", lambda u: GOOD_VTT)

    res = transcription.get_transcript(URL, lang="de", api_key=None)

    assert res.ok
    assert res.source == "auto"
    assert res.language == "de"


def test_missing_lang_falls_back_to_available(data_dir, monkeypatch):
    monkeypatch.setattr(
        transcription, "_extract_info", lambda url: make_info(subs=vtt_entry("manual")))
    monkeypatch.setattr(transcription, "_fetch_captions", lambda u: GOOD_VTT)

    res = transcription.get_transcript(URL, lang="de", api_key=None)

    assert res.ok
    assert res.language == "ru"


def test_playlist_entry_unwrapped(data_dir, monkeypatch):
    monkeypatch.setattr(
        transcription, "_extract_info",
        lambda url: make_info(subs=vtt_entry("manual")))
    monkeypatch.setattr(transcription, "_fetch_captions", lambda u: GOOD_VTT)

    res = transcription.get_transcript(
        "https://www.youtube.com/playlist?list=PLxxx", api_key=None)

    assert res.ok


def test_cache_load_failure_treated_as_miss(data_dir, monkeypatch):
    monkeypatch.setattr(
        transcription, "_extract_info",
        lambda url: make_info(subs=vtt_entry("manual")))
    monkeypatch.setattr(transcription, "_fetch_captions", lambda u: GOOD_VTT)

    def boom(video_id):
        raise RuntimeError("сломанный кэш")

    monkeypatch.setattr(transcription.cache, "load", boom)

    res = transcription.get_transcript(URL, api_key=None)

    assert res.ok
    assert res.source == "manual"
    assert res.segments[0].text == "Привет из субтитров"


def test_parse_failure_falls_to_auto(data_dir, monkeypatch):
    monkeypatch.setattr(
        transcription, "_extract_info",
        lambda url: make_info(subs=vtt_entry("manual"), auto=vtt_entry("auto")))
    real_parse_vtt = transcription.parse_vtt

    def fetch(url):
        if url.startswith("http://cap/manual"):
            return "BROKEN"
        return GOOD_VTT

    def parse_vtt(raw):
        if raw == "BROKEN":
            raise ValueError("сломанный VTT")
        return real_parse_vtt(raw)

    monkeypatch.setattr(transcription, "_fetch_captions", fetch)
    monkeypatch.setattr(transcription, "parse_vtt", parse_vtt)

    res = transcription.get_transcript(URL, api_key=None)

    assert res.ok
    assert res.source == "auto"


def test_auto_falls_back_across_languages_on_fetch_error(data_dir, monkeypatch):
    monkeypatch.setattr(
        transcription, "_extract_info",
        lambda url: make_info(subs={}, auto=vtt_entry("auto")))

    def fetch(url):
        if url.startswith("http://cap/auto/ru.vtt"):
            raise RuntimeError("429 Too Many Requests")
        return GOOD_VTT

    monkeypatch.setattr(transcription, "_fetch_captions", fetch)

    res = transcription.get_transcript(URL, api_key=None)

    assert res.ok
    assert res.error is None
    assert res.source == "auto"
    assert res.language == "en"
    assert res.segments[0].text == "Привет из субтитров"


def test_manual_falls_back_across_languages_within_source(data_dir, monkeypatch):
    monkeypatch.setattr(
        transcription, "_extract_info",
        lambda url: make_info(subs=vtt_entry("manual"), auto=vtt_entry("auto")))

    def fetch(url):
        if url.startswith("http://cap/manual/ru.vtt"):
            raise RuntimeError("403 from youtube")
        return GOOD_VTT

    monkeypatch.setattr(transcription, "_fetch_captions", fetch)

    res = transcription.get_transcript(URL, api_key=None)

    assert res.ok
    assert res.source == "manual"
    assert res.language == "en"


def test_explicit_lang_never_crosses_language_family(monkeypatch):
    table = {
        "de": [{"ext": "vtt", "url": "http://cap/auto/de.vtt"}],
        "en": [{"ext": "vtt", "url": "http://cap/auto/en.vtt"}],
    }

    def fetch(url):
        if url.endswith("/de.vtt"):
            raise RuntimeError("429")
        return GOOD_VTT

    monkeypatch.setattr(transcription, "_fetch_captions", fetch)

    res = transcription._from_subtitles(URL, "abc123def45", "T", {}, table, want="de")

    assert res is None


def test_language_candidates_order_when_want_none():
    table = {"en": [], "de": [], "ru": [], "fr": [], "es": []}

    assert transcription._language_candidates(table, want=None) == [
        "ru", "en", "de", "es", "fr",
    ]


def test_language_candidates_prefers_exact_then_base_match():
    table = {"en-US": [], "ru-RU": [], "de": [], "en": []}

    assert transcription._language_candidates(table, want=None) == [
        "ru-RU", "en", "en-US", "de",
    ]


def test_language_candidates_want_limits_to_family():
    table = {"en": [], "en-US": [], "en-GB": [], "ru": [], "de": []}

    got = transcription._language_candidates(table, want="en-US")

    assert set(got) == {"en", "en-US", "en-GB"}
    assert got[0] == "en-US"
    assert "ru" not in got and "de" not in got


def test_both_sources_fail_reports_no_subs_error(data_dir, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(
        transcription, "_extract_info",
        lambda url: make_info(subs=vtt_entry("manual"), auto=vtt_entry("auto")))

    def fetch(url):
        raise RuntimeError("429 from youtube")

    monkeypatch.setattr(transcription, "_fetch_captions", fetch)
    called = []
    monkeypatch.setattr(
        transcription, "_transcribe_via_whisper", lambda *a, **k: called.append(1))

    res = transcription.get_transcript(URL, api_key=None)

    assert not res.ok
    assert res.error == "нет транскриптов (нет субтитров, OPENAI_API_KEY не задан)"
    assert called == []


def test_cache_save_failure_still_returns_result(data_dir, monkeypatch):
    monkeypatch.setattr(
        transcription, "_extract_info",
        lambda url: make_info(subs=vtt_entry("manual")))
    monkeypatch.setattr(transcription, "_fetch_captions", lambda u: GOOD_VTT)

    def boom(video_id, data):
        raise OSError("нет места на диске")

    monkeypatch.setattr(transcription.cache, "save", boom)

    res = transcription.get_transcript(URL, api_key=None)

    assert res.ok
    assert res.source == "manual"
    assert res.segments[0].text == "Привет из субтитров"


def _fake_ydl(fail_first, info):
    class FakeYDL:
        calls = []

        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def extract_info(self, url, download=False):
            FakeYDL.calls.append(self.opts)
            if fail_first and "extractor_args" not in self.opts:
                raise RuntimeError("Sign in to confirm you're not a bot")
            return dict(info)

    return FakeYDL


def test_extract_info_retries_youtube_client_fallback(monkeypatch):
    fake = _fake_ydl(True, make_info(auto=vtt_entry("auto")))
    monkeypatch.setattr(transcription.yt_dlp, "YoutubeDL", fake)

    info = transcription._extract_info(URL)

    assert info["title"] == "Test video"
    assert len(fake.calls) >= 2
    args = fake.calls[1]["extractor_args"]["youtube"]["player_client"]
    assert args == ["tv_embedded"]


def test_extract_info_no_fallback_for_genuine_error(monkeypatch):
    class FailingYDL:
        def __init__(self, opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def extract_info(self, url, download=False):
            raise RuntimeError("приватное видео")

    monkeypatch.setattr(transcription.yt_dlp, "YoutubeDL", FailingYDL)

    with pytest.raises(RuntimeError, match="приватное видео"):
        transcription._extract_info(URL)
