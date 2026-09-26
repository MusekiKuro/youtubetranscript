from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from app import transcription

MAX_WORKERS = 5


@dataclass
class JobItem:
    url: str
    status: str = "pending"
    video_id: str | None = None
    title: str | None = None
    source: str | None = None
    language: str | None = None
    error: str | None = None


@dataclass
class Job:
    job_id: str
    lang: str | None
    status: str = "running"
    items: list[JobItem] = field(default_factory=list)


class JobStore:
    def __init__(self, workers: int = MAX_WORKERS):
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=workers)

    def create(self, urls: list[str], lang: str | None = None) -> str:
        job_id = uuid.uuid4().hex[:12]
        job = Job(job_id=job_id, lang=lang, items=[JobItem(url=u) for u in urls])
        with self._lock:
            self._jobs[job_id] = job
        for index in range(len(job.items)):
            self._pool.submit(self._run_item, job_id, index)
        return job_id

    def _run_item(self, job_id: str, index: int) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.items[index].status = "running"
            url = job.items[index].url
            lang = job.lang
        try:
            result = transcription.get_transcript(url, lang=lang)
        except Exception as exc:
            with self._lock:
                item = self._jobs[job_id].items[index]
                item.status = "error"
                item.error = f"внутренняя ошибка: {exc}"
        else:
            with self._lock:
                item = self._jobs[job_id].items[index]
                item.video_id = result.video_id
                item.title = result.title
                item.source = result.source
                item.language = result.language
                if result.error:
                    item.status = "error"
                    item.error = result.error
                else:
                    item.status = "done"
        with self._lock:
            job = self._jobs[job_id]
            if all(i.status in ("done", "error") for i in job.items):
                job.status = "done"

    def get(self, job_id: str) -> dict | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            return {
                "job_id": job.job_id,
                "status": job.status,
                "items": [
                    {
                        "url": item.url,
                        "status": item.status,
                        "video_id": item.video_id,
                        "title": item.title,
                        "source": item.source,
                        "language": item.language,
                        "error": item.error,
                    }
                    for item in job.items
                ],
            }
