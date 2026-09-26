import time
import zipfile
from io import BytesIO

import pytest
from fastapi.testclient import TestClient

from app import cache, transcription
from app.vtt import Segment


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(cache, "DATA_DIR", tmp_path)

    def fake_get_transcript(url, lang=None, api_key=None):
        vid = cache.cache_key(url)
        result = transcription.TranscriptResult(
            url=url, video_id=vid, title=f"Видео {vid}", source="manual",
            language="ru", segments=[Segment(0.0, 1.5, "привет мир")],
            available_languages=["ru", "en"],
        )
        cache.save(vid, result.to_cache())
        return result

    monkeypatch.setattr(transcription, "get_transcript", fake_get_transcript)
    from app.main import app
    return TestClient(app)


def poll_job(client, job_id, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] == "done":
            return job
        time.sleep(0.01)
    raise AssertionError(f"job did not finish: {job}")


def test_parse_poll_transcript_download_zip(client):
    resp = client.post("/api/parse", json={
        "links": ["https://youtu.be/aaaaaaaaaaa", "https://example.com/v/1"]})
    assert resp.status_code == 200
    job_id = resp.json()["job_id"]

    job = poll_job(client, job_id)
    assert [i["status"] for i in job["items"]] == ["done", "done"]
    vid = job["items"][0]["video_id"]

    transcript = client.get(f"/api/transcript/{vid}")
    assert transcript.status_code == 200
    body = transcript.json()
    assert body["segments"][0]["text"] == "привет мир"
    assert body["source"] == "manual"
    assert body["available_languages"] == ["ru", "en"]

    srt = client.get(f"/api/transcript/{vid}/download", params={"format": "srt"})
    assert srt.status_code == 200
    assert "attachment" in srt.headers["content-disposition"]
    assert "00:00:00,000 --> 00:00:01,500" in srt.text

    txt = client.get(f"/api/transcript/{vid}/download", params={"format": "txt"})
    assert "[00:00] привет мир" in txt.text

    zipped = client.get(f"/api/jobs/{job_id}/zip")
    assert zipped.status_code == 200
    assert zipped.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(BytesIO(zipped.content)) as zf:
        names = zf.namelist()
        assert len(names) == 2
        assert zf.read(names[0]).decode("utf-8").startswith("1\n00:00:00,000")


def test_zip_excludes_failed(client, monkeypatch):
    def half_ok(url, lang=None, api_key=None):
        if "bbbbbbbbbbb" in url:
            return transcription.TranscriptResult(
                url=url, video_id=cache.cache_key(url), error="нет транскриптов")
        vid = cache.cache_key(url)
        result = transcription.TranscriptResult(
            url=url, video_id=vid, title="Ок", source="auto", language="ru",
            segments=[Segment(0.0, 1.0, "текст")])
        cache.save(vid, result.to_cache())
        return result

    monkeypatch.setattr(transcription, "get_transcript", half_ok)
    resp = client.post("/api/parse", json={
        "links": ["https://youtu.be/aaaaaaaaaaa", "https://youtu.be/bbbbbbbbbbb"]})
    job = poll_job(client, resp.json()["job_id"])
    assert [i["status"] for i in job["items"]] == ["done", "error"]

    zipped = client.get(f"/api/jobs/{job['job_id']}/zip")
    assert zipped.status_code == 200
    with zipfile.ZipFile(BytesIO(zipped.content)) as zf:
        assert len(zf.namelist()) == 1


def test_rejects_bad_links(client):
    assert client.post("/api/parse", json={"links": []}).status_code == 422
    assert client.post("/api/parse", json={
        "links": ["ftp://example.com/x"]}).status_code == 400
    assert client.post("/api/parse", json={
        "links": ["notaurl"]}).status_code == 400
    assert client.post("/api/parse", json={
        "links": [f"https://example.com/{i}" for i in range(51)]}).status_code == 422


def test_unknown_job_404(client):
    assert client.get("/api/jobs/doesnotexist").status_code == 404


def test_transcript_rejects_bad_id(client, monkeypatch, tmp_path):
    monkeypatch.setattr(cache, "DATA_DIR", tmp_path)
    assert client.get("/api/transcript/..").status_code == 400
    assert client.get("/api/transcript/..%2F..%2Fevil").status_code in (400, 404)
    assert list(tmp_path.glob("*")) == []


def test_transcript_missing_404(client, tmp_path):
    assert client.get("/api/transcript/jNQXAC9IVRw").status_code == 404


def test_bad_download_format_400(client):
    resp = client.post("/api/parse", json={"links": ["https://youtu.be/aaaaaaaaaaa"]})
    job = poll_job(client, resp.json()["job_id"])
    vid = job["items"][0]["video_id"]
    assert client.get(
        f"/api/transcript/{vid}/download", params={"format": "exe"}
    ).status_code == 400
