from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

_YOUTUBE_RE = re.compile(
    r"(?:youtube\.com/(?:watch\?(?:.*&)?v=|shorts/|live/|embed/)|youtu\.be/)"
    r"(?P<id>[\w-]{11})"
)
_SAFE_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")


def cache_key(url: str) -> str:
    match = _YOUTUBE_RE.search(url)
    if match:
        return match.group("id")
    normalized = url.strip().rstrip("/")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def _path(video_id: str) -> Path:
    if not _SAFE_ID_RE.match(video_id):
        raise ValueError("bad video id")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = (DATA_DIR / f"{video_id}.json").resolve()
    if path.parent != DATA_DIR.resolve():
        raise ValueError("bad video id")
    return path


def load(video_id: str) -> dict | None:
    path = _path(video_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def save(video_id: str, data: dict) -> None:
    path = _path(video_id)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp_name, path)
    except BaseException:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise
