from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import yt_dlp

from app import cache, whisper_client
from app.vtt import Segment, parse_srt, parse_vtt

logger = logging.getLogger(__name__)

DEFAULT_LANG_PREFERENCE = ("ru", "en")
_CAPTION_EXTS = ("vtt", "srt")


@dataclass
class TranscriptResult:
    url: str
    video_id: str
    title: str = ""
    source: str | None = None
    language: str | None = None
    segments: list[Segment] = field(default_factory=list)
    available_languages: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and bool(self.segments)

    def to_cache(self) -> dict:
        return {
            "url": self.url,
            "video_id": self.video_id,
            "title": self.title,
            "source": self.source,
            "language": self.language,
            "available_languages": self.available_languages,
            "segments": [
                {"start": s.start, "end": s.end, "text": s.text} for s in self.segments
            ],
        }

    @classmethod
    def from_cache(cls, data: dict) -> "TranscriptResult":
        return cls(
            url=data["url"],
            video_id=data.get("video_id") or cache.cache_key(data["url"]),
            title=data.get("title", ""),
            source=data.get("source"),
            language=data.get("language"),
            segments=[
                Segment(s["start"], s["end"], s["text"]) for s in data.get("segments", [])
            ],
            available_languages=data.get("available_languages", []),
        )


def _extract_info(url: str) -> dict:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    if info.get("_type") == "playlist":
        entries = [e for e in (info.get("entries") or []) if e]
        if not entries:
            raise RuntimeError("плейлист пуст")
        info = entries[0]
    return info


def _fetch_captions(url: str) -> str:
    response = httpx.get(url, timeout=30, follow_redirects=True)
    response.raise_for_status()
    return response.text


def _language_candidates(table, want: str | None) -> list[str]:
    keys = list(table)
    if not keys:
        return []
    if want:
        base = want.split("-")[0]
        family = [k for k in keys if k.split("-")[0] == base]
        family.sort(key=lambda k: (k != want, k))
        return family
    ordered: list[str] = []
    for pref in DEFAULT_LANG_PREFERENCE:
        if pref in keys:
            ordered.append(pref)
        ordered.extend(
            k for k in sorted(keys)
            if k.split("-")[0] == pref and k not in ordered
        )
    return ordered + [k for k in sorted(keys) if k not in ordered]


def _pick_language(keys: list, want: str | None) -> str | None:
    candidates = _language_candidates(keys, want)
    return candidates[0] if candidates else None


def _pick_format(formats: list) -> dict | None:
    for ext in _CAPTION_EXTS:
        for fmt in formats:
            if fmt.get("ext") == ext and fmt.get("url"):
                return fmt
    return None


def _from_subtitles(
    url: str,
    video_id: str,
    title: str,
    manual: dict,
    auto: dict,
    want: str | None,
) -> TranscriptResult | None:
    available = sorted(set(manual) | set(auto))
    for source_name, table in (("manual", manual), ("auto", auto)):
        if not table:
            continue
        for lang_key in _language_candidates(table, want):
            fmt = _pick_format(table[lang_key])
            if not fmt:
                continue
            try:
                raw = _fetch_captions(fmt["url"])
                segments = parse_vtt(raw) if fmt["ext"] == "vtt" else parse_srt(raw)
            except Exception as exc:
                logger.warning("caption fetch/parse failed for %s: %s", url, exc)
                continue
            if not segments:
                logger.warning("empty captions for %s (%s/%s)", url, source_name, lang_key)
                continue
            return TranscriptResult(
                url=url, video_id=video_id, title=title, source=source_name,
                language=lang_key, segments=segments, available_languages=available,
            )
    return None


def _transcribe_via_whisper(
    url: str, lang: str | None, api_key: str
) -> tuple[list[Segment], str | None]:
    with tempfile.TemporaryDirectory(prefix="transcript-audio-") as tmp:
        tmp_path = Path(tmp)
        opts = {
            "format": "bestaudio/best",
            "outtmpl": str(tmp_path / "audio.%(ext)s"),
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
        candidates = sorted(tmp_path.glob("audio.*"))
        if not candidates:
            raise RuntimeError("аудио не скачалось")
        source = candidates[0]
        mp3 = tmp_path / "audio.mp3"
        if source != mp3:
            subprocess.run(
                ["ffmpeg", "-y", "-i", str(source), "-vn", "-ac", "1",
                 "-ar", "16000", "-b:a", "48k", str(mp3)],
                check=True, capture_output=True,
            )
        srt_text = whisper_client.transcribe_srt(mp3, api_key, language=lang)
    return parse_srt(srt_text), lang


def _save_cache(video_id: str, result: TranscriptResult) -> None:
    try:
        cache.save(video_id, result.to_cache())
    except Exception as exc:
        logger.warning("cache write failed for %s: %s", video_id, exc)


def get_transcript(
    url: str, lang: str | None = None, api_key: str | None = None
) -> TranscriptResult:
    if api_key is None:
        api_key = os.environ.get("OPENAI_API_KEY") or None
    video_id = cache.cache_key(url)

    try:
        hit = cache.load(video_id)
        if hit:
            hit_result = TranscriptResult.from_cache(hit)
            if (
                hit_result.source == "whisper"
                or lang is None
                or hit_result.language == lang
            ):
                return hit_result
    except Exception as exc:
        logger.warning("cache read failed for %s: %s", video_id, exc)

    try:
        info = _extract_info(url)
    except Exception as exc:
        return TranscriptResult(
            url=url, video_id=video_id, error=f"не удалось получить видео: {exc}"
        )

    title = info.get("title") or video_id
    manual = info.get("subtitles") or {}
    auto = info.get("automatic_captions") or {}

    result = _from_subtitles(url, video_id, title, manual, auto, want=lang)
    if result is None and lang:
        result = _from_subtitles(url, video_id, title, manual, auto, want=None)
    if result is not None:
        _save_cache(video_id, result)
        return result

    available = sorted(set(manual) | set(auto))
    if not api_key:
        return TranscriptResult(
            url=url, video_id=video_id, title=title, available_languages=available,
            error="нет транскриптов (нет субтитров, OPENAI_API_KEY не задан)",
        )
    try:
        segments, used_lang = _transcribe_via_whisper(url, lang, api_key)
    except Exception as exc:
        logger.warning("whisper failed for %s: %s", url, exc)
        return TranscriptResult(
            url=url, video_id=video_id, title=title, available_languages=available,
            error=f"нет транскриптов (Whisper недоступен: {exc})",
        )
    if not segments:
        return TranscriptResult(
            url=url, video_id=video_id, title=title, available_languages=available,
            error="нет транскриптов (пустая расшифровка)",
        )
    result = TranscriptResult(
        url=url, video_id=video_id, title=title, source="whisper",
        language=used_lang or lang, segments=segments, available_languages=available,
    )
    _save_cache(video_id, result)
    return result
