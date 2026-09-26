from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from app import cache, transcription
from app.batch import JobStore
from app.vtt import Segment, segments_to_srt, segments_to_text

load_dotenv()

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
MAX_LINKS = 50

app = FastAPI(title="Парсер транскриптов")
store = JobStore()


class ParseRequest(BaseModel):
    links: list[str] = Field(min_length=1, max_length=MAX_LINKS)
    lang: str | None = None


def _valid_url(url: str) -> bool:
    return url.startswith(("http://", "https://")) and len(url) > 8


def _segments_from_cache(data: dict) -> list[Segment]:
    return [Segment(s["start"], s["end"], s["text"]) for s in data.get("segments", [])]


def _safe_filename(title: str, video_id: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|]+', "_", title)
    cleaned = re.sub(r"[\x00-\x1f\x7f-\x9f]+", "_", cleaned).strip(" .")
    return (cleaned or video_id)[:100]


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html", media_type="text/html")


@app.post("/api/parse")
def parse(req: ParseRequest) -> dict:
    bad = [u for u in req.links if not _valid_url(u)]
    if bad:
        raise HTTPException(400, f"некорректная ссылка: {bad[0]}")
    job_id = store.create(req.links, lang=req.lang)
    return {"job_id": job_id}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str) -> dict:
    job = store.get(job_id)
    if job is None:
        raise HTTPException(404, "job не найден")
    return job


@app.get("/api/transcript/{video_id}")
def transcript(video_id: str, lang: str | None = None):
    try:
        data = cache.load(video_id)
    except ValueError:
        raise HTTPException(400, "некорректный video_id")
    if data is None:
        raise HTTPException(404, "транскрипт не найден")
    if lang and data.get("language") != lang and data.get("source") != "whisper":
        result = transcription.get_transcript(data["url"], lang=lang)
        if result.ok:
            data = result.to_cache()
        # если рефетч не удался — отдаём закэшированную версию
    return data


@app.get("/api/transcript/{video_id}/download")
def download(video_id: str, format: str = "srt"):
    if format not in ("srt", "txt"):
        raise HTTPException(400, "format должен быть srt или txt")
    try:
        data = cache.load(video_id)
    except ValueError:
        raise HTTPException(400, "некорректный video_id")
    if data is None:
        raise HTTPException(404, "транскрипт не найден")
    segments = _segments_from_cache(data)
    body = segments_to_srt(segments) if format == "srt" else segments_to_text(segments)
    filename = f"{_safe_filename(data.get('title', ''), video_id)}.{format}"
    ascii_name = filename if filename.isascii() else f"{video_id}.{format}"
    disposition = (
        f'attachment; filename="{ascii_name}"; '
        f"filename*=UTF-8''{quote(filename, safe='')}"
    )
    return Response(
        body,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": disposition},
    )


@app.get("/api/jobs/{job_id}/zip")
def job_zip(job_id: str):
    job = store.get(job_id)
    if job is None:
        raise HTTPException(404, "job не найден")
    buffer = io.BytesIO()
    written = 0
    used_names: set[str] = set()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for item in job["items"]:
            if item["status"] != "done" or not item.get("video_id"):
                continue
            try:
                data = cache.load(item["video_id"])
            except ValueError:
                continue
            if not data:
                continue
            name = f"{_safe_filename(data.get('title', ''), item['video_id'])}.srt"
            if name in used_names:
                name = f"{item['video_id']}.srt"
            used_names.add(name)
            zf.writestr(name, segments_to_srt(_segments_from_cache(data)))
            written += 1
    if written == 0:
        raise HTTPException(404, "нет готовых транскриптов")
    buffer.seek(0)
    return Response(
        buffer.read(),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="transcripts-{job_id}.zip"'
        },
    )
