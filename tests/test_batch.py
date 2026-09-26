import time

from app import batch, transcription
from app.vtt import Segment


def wait_done(store, job_id, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = store.get(job_id)
        if job and job["status"] == "done":
            return job
        time.sleep(0.01)
    raise AssertionError(f"job {job_id} did not finish: {store.get(job_id)}")


def fake_ok(url, lang=None, api_key=None):
    vid = transcription.cache.cache_key(url)
    return transcription.TranscriptResult(
        url=url, video_id=vid, title=f"video {vid}", source="manual",
        language="ru", segments=[Segment(0.0, 1.0, "текст")],
    )


def test_all_urls_complete(monkeypatch):
    monkeypatch.setattr(transcription, "get_transcript", fake_ok)
    store = batch.JobStore(workers=3)
    urls = ["https://youtu.be/aaaaaaaaaaa", "https://youtu.be/bbbbbbbbbbb"]

    job_id = store.create(urls)
    job = wait_done(store, job_id)

    assert job["status"] == "done"
    assert [i["status"] for i in job["items"]] == ["done", "done"]
    assert job["items"][0]["title"].startswith("video ")
    assert job["items"][0]["video_id"] == "aaaaaaaaaaa"


def test_one_failure_does_not_block_others(monkeypatch):
    def flaky(url, lang=None, api_key=None):
        if "bbbbbbbbbbb" in url:
            raise RuntimeError("взрыв парсера")
        return fake_ok(url, lang, api_key)

    monkeypatch.setattr(transcription, "get_transcript", flaky)
    store = batch.JobStore(workers=3)
    urls = [
        "https://youtu.be/aaaaaaaaaaa",
        "https://youtu.be/bbbbbbbbbbb",
        "https://youtu.be/ccccccccccc",
    ]

    job_id = store.create(urls)
    job = wait_done(store, job_id)

    statuses = {i["url"].rsplit("/", 1)[-1]: i["status"] for i in job["items"]}
    assert statuses["aaaaaaaaaaa"] == "done"
    assert statuses["bbbbbbbbbbb"] == "error"
    assert statuses["ccccccccccc"] == "done"
    failed = next(i for i in job["items"] if i["status"] == "error")
    assert "внутренняя ошибка" in failed["error"]
    assert "взрыв парсера" in failed["error"]


def test_result_error_field_marks_item_failed(monkeypatch):
    def with_error(url, lang=None, api_key=None):
        return transcription.TranscriptResult(url=url, video_id="x", error="нет транскриптов")

    monkeypatch.setattr(transcription, "get_transcript", with_error)
    store = batch.JobStore(workers=1)

    job_id = store.create(["https://youtu.be/aaaaaaaaaaa"])
    job = wait_done(store, job_id)

    assert job["items"][0]["status"] == "error"
    assert job["items"][0]["error"] == "нет транскриптов"


def test_lang_passed_through(monkeypatch):
    seen = []

    def capture(url, lang=None, api_key=None):
        seen.append(lang)
        return fake_ok(url, lang, api_key)

    monkeypatch.setattr(transcription, "get_transcript", capture)
    store = batch.JobStore(workers=1)

    job_id = store.create(["https://youtu.be/aaaaaaaaaaa"], lang="de")
    wait_done(store, job_id)

    assert seen == ["de"]


def test_get_unknown_job_returns_none():
    store = batch.JobStore()
    assert store.get("nope") is None


def test_empty_url_list_job_is_done_immediately():
    store = batch.JobStore()

    job_id = store.create([])
    job = store.get(job_id)

    assert job["status"] == "done"
    assert job["items"] == []
