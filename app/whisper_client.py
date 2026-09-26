from __future__ import annotations

import time
from pathlib import Path

import httpx

OPENAI_TRANSCRIPTIONS_URL = "https://api.openai.com/v1/audio/transcriptions"
_NO_RETRY_STATUS = {400, 401, 403, 422}
_BACKOFF = (1, 3)


class WhisperError(Exception):
    pass


def transcribe_srt(
    audio_path: Path,
    api_key: str,
    *,
    language: str | None = None,
    client: httpx.Client | None = None,
    max_attempts: int = 3,
) -> str:
    owns_client = client is None
    http = client or httpx.Client(timeout=120)
    last_error: Exception | None = None
    try:
        for attempt in range(max_attempts):
            try:
                with open(audio_path, "rb") as fh:
                    data = {"model": "whisper-1", "response_format": "srt"}
                    if language:
                        data["language"] = language
                    response = http.post(
                        OPENAI_TRANSCRIPTIONS_URL,
                        data=data,
                        files={"file": (audio_path.name, fh, "audio/mpeg")},
                        headers={"Authorization": f"Bearer {api_key}"},
                    )
            except httpx.HTTPError as exc:
                last_error = exc
            else:
                if response.status_code == 200:
                    return response.text
                message = f"Whisper API {response.status_code}: {response.text[:300]}"
                if response.status_code in _NO_RETRY_STATUS:
                    raise WhisperError(message)
                last_error = WhisperError(message)
            if attempt < max_attempts - 1:
                time.sleep(_BACKOFF[min(attempt, len(_BACKOFF) - 1)])
        raise WhisperError(f"после {max_attempts} попыток: {last_error}")
    finally:
        if owns_client:
            http.close()
