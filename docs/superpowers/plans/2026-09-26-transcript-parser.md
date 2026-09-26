# Transcript Parser Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Веб-приложение, которое по списку ссылок на видео извлекает транскрипты (авторские субтитры → автосубтитры → Whisper API) и отдаёт их на страницу с таймкодами, в .srt/.txt и ZIP.

**Architecture:** FastAPI-сервер с одним HTML-файлом UI. Ядро — цепочка источников транскриптов (manual subs → auto subs → Whisper API → «нет транскриптов»), кэш в JSON-файлах `data/`, пакетная обработка через ThreadPoolExecutor(5) с поллингом статуса из браузера.

**Tech Stack:** Python 3.14, FastAPI, uvicorn, yt-dlp (Python API), httpx, python-dotenv, ffmpeg, pytest. Фронтенд — один HTML, ванильный JS, без сборки.

**Spec:** `docs/superpowers/specs/2026-09-26-transcript-parser-design.md`

## Global Constraints

- Окружение: `.venv` в корне проекта (создан с `--system-site-packages`); ВСЕ команды — через `.venv/bin/*` (`.venv/bin/python`, `.venv/bin/pip`). PEP 668: системный pip ставить пакеты не может.
- Python 3.14; pytest 8.3, httpx 0.27, FastAPI, uvicorn, python-dotenv уже доступны через system-site-packages; yt-dlp 2026.08.19 установлен в `.venv`.
- Порядок источников строго: ручные субтитры (`subtitles`) → автосубтитры (`automatic_captions`) → Whisper API → статус «нет транскриптов».
- `OPENAI_API_KEY` читается только из окружения/`.env` (python-dotenv); ключ и `.env` никогда не попадают в git (`.env` в `.gitignore`).
- Whisper: `https://api.openai.com/v1/audio/transcriptions`, модель `whisper-1`, `response_format=srt`; ретраи максимум 3 попытки с паузами 1с и 3с; на 400/401/403/422 — без ретраев.
- Пакетная обработка: `ThreadPoolExecutor(max_workers=5)`; один POST-запрос — от 1 до 50 ссылок.
- Кэш: `data/{video_id}.json`; `video_id` = 11-символьный YouTube-id из URL, иначе `sha256(-url-)-[:16]`; невалидный id → `ValueError`, путь никогда не покидает `data/`.
- Сервер слушает только `127.0.0.1`.
- Ссылки принимаются только `http://` / `https://` с непустым хостом.
- Форматы вывода: TXT — `[MM:SS] реплика` (часы `HH:MM:SS` при длительности ≥ 1 часа); SRT — нумерованные блоки через пустую строку, `HH:MM:SS,mmm --> HH:MM:SS,mmm`.
- Фронтенд: один `static/index.html`, ванильный JS, без фреймворков и сборки. Никаких Celery/БД: кэш — файлы, job'ы — в памяти.
- ffmpeg вызывается как `ffmpeg -y -i <src> -vn -ac 1 -ar 16000 -b:a 48k <dst.mp3>` (аудио ≤ 25 МБ для Whisper).

## Review Focus

Пять входов/условий, которые ломаются раньше всего; у каждого — задача и тест, закрепляющий ожидание:

1. **Автосубтры YouTube с roll-up дублями** (реплики `hello` / `hello world` / `hello world this is`) → ожидаю чистый текст без повторов. — Task 2, `test_rollup_context_stripped`.
2. **Видео без субтитров и без `OPENAI_API_KEY`** → ожидаю статус «нет транскриптов» в ответе, без исключения наружу и зависания. — Task 5, `test_no_subs_without_key`.
3. **Whisper API отвечает 429/5xx или падает сеть** → ожидаю ровно 3 попытки с паузами 1с/3с, затем `WhisperError`; 401 — сразу отказ. — Task 4, `test_retries_then_fails`, `test_auth_error_no_retry`.
4. **`video_id` с path traversal (`..`, `%2F`)** → ожидаю `ValueError` на уровне кэша и HTTP 400 от API; файл вне `data/` не создаётся. — Task 3, `test_path_traversal_rejected`; Task 7, `test_transcript_rejects_bad_id`.
5. **Одна битая ссылка в пачке из трёх** → ожидаю: две других `done`, сломанная `error`, job завершается, ZIP содержит только готовые. — Task 6, `test_one_failure_does_not_block_others`; Task 7, `test_zip_excludes_failed`.

---

### Task 1: Каркас проекта

**Files:**
- Create: `.gitignore`, `requirements.txt`, `pytest.ini`, `.env.example`, `app/__init__.py`
- Test: `tests/test_scaffold.py`

**Interfaces:**
- Consumes: ничего (первая задача)
- Produces: пакет `app/` (импортируется как `app.*`), конфиг pytest (`pythonpath = .`), git-репозиторий. Все дальнейшие задачи пишут код в `app/`, тесты — в `tests/`.

- [ ] **Step 1: Проверить `.venv`**

```bash
.venv/bin/python -c "import fastapi, uvicorn, httpx, dotenv, pytest, yt_dlp; print('ok')"
```

Expected: `ok`

- [ ] **Step 2: Создать файлы проекта**

`.gitignore`:
```
.venv/
__pycache__/
*.pyc
.pytest_cache/
.env
data/
```

`requirements.txt`:
```
fastapi
uvicorn
yt-dlp
httpx
python-dotenv
pytest
```

`pytest.ini`:
```ini
[pytest]
testpaths = tests
pythonpath = .
```

`.env.example`:
```
# Скопируйте в .env и впишите ключ
OPENAI_API_KEY=
```

`app/__init__.py` — пустой файл.

- [ ] **Step 3: Написать smoke-тест**

`tests/test_scaffold.py`:
```python
def test_app_package_importable():
    import app
    assert app is not None
```

- [ ] **Step 4: Запустить тесты**

Run: `.venv/bin/python -m pytest -v`
Expected: 1 passed

- [ ] **Step 5: Git init и первый коммит**

```bash
git init
git add .gitignore requirements.txt pytest.ini .env.example app/__init__.py tests/test_scaffold.py
git commit -m "chore: project scaffolding"
```

Expected: commit создан; `.venv/` и `data/` не попали в индекс.

---

### Task 2: Парсер VTT/SRT → сегменты с таймкодами

Ядро проекта: чистка HTML-тегов, разбор таймингов, схлопывание roll-up дублей автосубтитров, форматирование обратно в TXT и SRT.

**Files:**
- Create: `app/vtt.py`
- Test: `tests/test_vtt.py`
- Fixture: `tests/fixtures/rollup.vtt`

**Interfaces:**
- Consumes: ничего
- Produces (точные имена, на них опираются Tasks 5, 7):
  - `@dataclass Segment(start: float, end: float, text: str)` — секунды (float), текст без HTML-тегов
  - `parse_vtt(content: str) -> list[Segment]`
  - `parse_srt(content: str) -> list[Segment]`
  - `segments_to_srt(segments: list[Segment]) -> str`
  - `segments_to_text(segments: list[Segment]) -> str`

- [ ] **Step 1: Написать фикстуру roll-up**

`tests/fixtures/rollup.vtt`:
```text
WEBVTT
Kind: captions
Language: en

00:00:00.000 --> 00:00:02.000
hello

00:00:02.000 --> 00:00:04.000
hello
world

00:00:04.000 --> 00:00:06.000
hello world
this is
```

- [ ] **Step 2: Написать падающие тесты**

`tests/test_vtt.py`:
```python
from pathlib import Path

from app.vtt import parse_srt, parse_vtt, segments_to_srt, segments_to_text

FIXTURES = Path(__file__).parent / "fixtures"

CLEAN_VTT = """WEBVTT

00:00:01.000 --> 00:00:03.500
Hello <c>world</c>

00:00:03.500 --> 00:00:06.000
Second &amp; last cue
"""

SRT_SAMPLE = """1
00:00:01,000 --> 00:00:03,500
Первая реплика

2
00:01:03,250 --> 00:01:05,000
Вторая реплика
"""


def test_parse_vtt_cleans_tags_and_entities():
    segs = parse_vtt(CLEAN_VTT)
    assert len(segs) == 2
    assert segs[0].text == "Hello world"
    assert segs[0].start == 1.0
    assert segs[0].end == 3.5
    assert segs[1].text == "Second & last cue"


def test_rollup_context_stripped():
    content = (FIXTURES / "rollup.vtt").read_text(encoding="utf-8")
    segs = parse_vtt(content)
    assert [s.text for s in segs] == ["hello", "world", "this is"]
    assert segs[1].start == 2.0


def test_parse_srt_timings():
    segs = parse_srt(SRT_SAMPLE)
    assert [s.text for s in segs] == ["Первая реплика", "Вторая реплика"]
    assert segs[1].start == 63.25


def test_empty_input():
    assert parse_vtt("") == []
    assert parse_srt("WEBVTT\n") == []


def test_cue_with_repeated_single_line_is_kept():
    content = (
        "WEBVTT\n\n"
        "00:00:00.000 --> 00:00:01.000\nПривет\n\n"
        "00:00:01.000 --> 00:00:02.000\nПривет\n"
    )
    segs = parse_vtt(content)
    assert [s.text for s in segs] == ["Привет", "Привет"]


def test_segments_to_srt_format():
    segs = parse_srt(SRT_SAMPLE)
    out = segments_to_srt(segs)
    assert out.startswith("1\n00:00:01,000 --> 00:00:03,500\nПервая реплика\n\n2\n")
    assert "00:01:03,250 --> 00:01:05,000" in out


def test_segments_to_text_timestamps():
    segs = parse_srt(SRT_SAMPLE)
    out = segments_to_text(segs)
    assert out.splitlines() == ["[00:01] Первая реплика", "[01:03] Вторая реплика"]

    long_seg = [type(segs[0])(4000.0, 4001.0, "часы")]
    assert segments_to_text(long_seg) == "[01:06:40] часы"
```

- [ ] **Step 3: Запустить и убедиться, что падает**

Run: `.venv/bin/python -m pytest tests/test_vtt.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.vtt'`

- [ ] **Step 4: Реализовать `app/vtt.py`**

```python
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class Segment:
    start: float
    end: float
    text: str


_TIMING_RE = re.compile(
    r"(?P<h1>\d{1,2}):(?P<m1>\d{2}):(?P<s1>\d{2})[.,](?P<ms1>\d{1,3})"
    r"\s*-->\s*"
    r"(?P<h2>\d{1,2}):(?P<m2>\d{2}):(?P<s2>\d{2})[.,](?P<ms2>\d{1,3})"
)
_TAG_RE = re.compile(r"<[^>]+>")
_ENTITY_RE = re.compile(r"&(#?\w+);")
_ENTITIES = {"amp": "&", "lt": "<", "gt": ">", "quot": '"', "apos": "'", "nbsp": " "}


def _replace_entity(match: re.Match) -> str:
    name = match.group(1)
    if name.startswith("#"):
        try:
            return chr(int(name[1:]))
        except ValueError:
            return " "
    return _ENTITIES.get(name, " ")


def _clean(line: str) -> str:
    line = _TAG_RE.sub("", line)
    line = _ENTITY_RE.sub(_replace_entity, line)
    return line.strip()


def _seconds(h: str, m: str, s: str, ms: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000


def _strip_rollup(current: str, previous_full: str) -> str:
    """Автосубтитры: каждая реплика повторяет предыдущую целиком (roll-up).

    Если текущая реплика начинается с полного текста предыдущей — отрезаем
    этот контекст. Пустой результат не принимаем: настоящее повторение речи
    (одинаковая одиночная реплика) остаётся как есть."""
    if previous_full and current.startswith(previous_full) and current != previous_full:
        remainder = current[len(previous_full):].strip()
        if remainder:
            return remainder
    return current


def parse_timed_text(content: str) -> list:
    segments = []
    previous_full = ""
    lines = content.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    i = 0
    while i < len(lines):
        match = _TIMING_RE.search(lines[i])
        if not match:
            i += 1
            continue
        start = _seconds(match["h1"], match["m1"], match["s1"], match["ms1"])
        end = _seconds(match["h2"], match["m2"], match["s2"], match["ms2"])
        i += 1
        block = []
        while i < len(lines) and lines[i].strip() and not _TIMING_RE.search(lines[i]):
            cleaned = _clean(lines[i])
            if cleaned:
                block.append(cleaned)
            i += 1
        full = " ".join(block)
        if not full:
            continue
        text = _strip_rollup(full, previous_full)
        previous_full = full
        segments.append(Segment(start=start, end=end, text=text))
    return segments


def parse_vtt(content: str) -> list:
    return parse_timed_text(content)


def parse_srt(content: str) -> list:
    return parse_timed_text(content)


def _fmt_clock(seconds: float) -> str:
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def _fmt_srt_clock(seconds: float) -> str:
    ms_total = int(round(seconds * 1000))
    h, rem = divmod(ms_total, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def segments_to_srt(segments: list) -> str:
    blocks = []
    for n, seg in enumerate(segments, start=1):
        blocks.append(
            f"{n}\n{_fmt_srt_clock(seg.start)} --> {_fmt_srt_clock(seg.end)}\n{seg.text}\n"
        )
    return "\n".join(blocks)


def segments_to_text(segments: list) -> str:
    return "\n".join(f"[{_fmt_clock(seg.start)}] {seg.text}" for seg in segments)
```

- [ ] **Step 5: Запустить тесты**

Run: `.venv/bin/python -m pytest tests/test_vtt.py -v`
Expected: 7 passed

- [ ] **Step 6: Коммит**

```bash
git add app/vtt.py tests/test_vtt.py tests/fixtures/rollup.vtt
git commit -m "feat: vtt/srt parser with rollup dedup and formatters"
```

---

### Task 3: Кэш в `data/` с защитой от path traversal

**Files:**
- Create: `app/cache.py`
- Test: `tests/test_cache.py`

**Interfaces:**
- Consumes: ничего (чистый модуль)
- Produces (используют Tasks 5, 7):
  - `DATA_DIR: Path` — модульный атрибут (тесты подменяют: `monkeypatch.setattr(cache, "DATA_DIR", tmp_path)`)
  - `cache_key(url: str) -> str`
  - `load(video_id: str) -> dict | None`
  - `save(video_id: str, data: dict) -> None` — атомарная запись (temp-файл + `os.replace`)
  - Невалидный id → `ValueError("bad video id")` из внутреннего `_path(video_id) -> Path`

- [ ] **Step 1: Написать падающие тесты**

`tests/test_cache.py`:
```python
import pytest

from app import cache


def test_youtube_id_extracted():
    assert cache.cache_key("https://youtu.be/jNQXAC9IVRw") == "jNQXAC9IVRw"
    assert cache.cache_key("https://www.youtube.com/watch?v=jNQXAC9IVRw&t=5") == "jNQXAC9IVRw"
    assert cache.cache_key("https://www.youtube.com/shorts/jNQXAC9IVRw") == "jNQXAC9IVRw"


def test_non_youtube_url_hash_stable_and_safe():
    a = cache.cache_key("https://example.com/video/123?x=1")
    b = cache.cache_key("https://example.com/video/123?x=1")
    c = cache.cache_key("https://example.com/video/456?x=1")
    assert a == b
    assert a != c
    assert len(a) == 16 and a.isalnum()


def test_save_load_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "DATA_DIR", tmp_path)
    payload = {
        "url": "https://youtu.be/jNQXAC9IVRw",
        "segments": [{"start": 0.0, "end": 1.0, "text": "hi"}],
    }
    cache.save("jNQXAC9IVRw", payload)
    assert cache.load("jNQXAC9IVRw") == payload
    assert cache.load("nonexistent1") is None


def test_path_traversal_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "DATA_DIR", tmp_path)
    for bad in ("..", "../evil", "..%2Fevil", "a/b", "x" * 65, "точка"):
        with pytest.raises(ValueError):
            cache._path(bad)
    assert list(tmp_path.iterdir()) == []


def test_missing_file_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "DATA_DIR", tmp_path)
    assert cache.load("jNQXAC9IVRw") is None
```

- [ ] **Step 2: Запустить и убедиться, что падает**

Run: `.venv/bin/python -m pytest tests/test_cache.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.cache'`

- [ ] **Step 3: Реализовать `app/cache.py`**

```python
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
_SAFE_ID_RE = re.compile(r"[\w-]{1,64}\Z")


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
```

- [ ] **Step 4: Запустить тесты**

Run: `.venv/bin/python -m pytest tests/test_cache.py -v`
Expected: 5 passed

- [ ] **Step 5: Коммит**

```bash
git add app/cache.py tests/test_cache.py
git commit -m "feat: json transcript cache with path traversal guard"
```

---

### Task 4: Whisper API клиент (OpenAI) с ретраями

**Files:**
- Create: `app/whisper_client.py`
- Test: `tests/test_whisper_client.py`

**Interfaces:**
- Consumes: httpx (уже установлен)
- Produces (использует Task 5):
  - `class WhisperError(Exception)`
  - `OPENAI_TRANSCRIPTIONS_URL: str`
  - `transcribe_srt(audio_path: Path, api_key: str, *, language: str | None = None, client: httpx.Client | None = None, max_attempts: int = 3) -> str` — возвращает текст SRT. Ретраи только на 429/5xx/сетевые ошибки (паузы 1с, 3с); 400/401/403/422 → `WhisperError` сразу. `client` — шов для тестов; при `client=None` свой клиент создаётся и закрывается.

- [ ] **Step 1: Написать падающие тесты**

`tests/test_whisper_client.py`:
```python
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
```

- [ ] **Step 2: Запустить и убедиться, что падает**

Run: `.venv/bin/python -m pytest tests/test_whisper_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.whisper_client'`

- [ ] **Step 3: Реализовать `app/whisper_client.py`**

```python
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
```

- [ ] **Step 4: Запустить тесты**

Run: `.venv/bin/python -m pytest tests/test_whisper_client.py -v`
Expected: 4 passed

- [ ] **Step 5: Коммит**

```bash
git add app/whisper_client.py tests/test_whisper_client.py
git commit -m "feat: openai whisper client with bounded retries"
```

---

### Task 5: Цепочка источников транскриптов

**Files:**
- Create: `app/transcription.py`
- Test: `tests/test_transcription.py`

**Interfaces:**
- Consumes:
  - `app.vtt`: `Segment`, `parse_vtt`, `parse_srt`
  - `app.cache`: `cache_key`, `load`, `save` (вызовы через `transcription.cache.*` — так тесты подменяют `DATA_DIR`)
  - `app.whisper_client`: `transcribe_srt`, `WhisperError`
  - yt-dlp (`yt_dlp.YoutubeDL`), httpx, `subprocess` + ffmpeg
- Produces (используют Tasks 6, 7):
  - `@dataclass TranscriptResult(url: str, video_id: str, title: str, source: str | None, language: str | None, segments: list[Segment], available_languages: list[str], error: str | None)` + свойство `ok: bool` + `to_cache() -> dict` + `TranscriptResult.from_cache(data: dict) -> TranscriptResult`
  - `get_transcript(url: str, lang: str | None = None, api_key: str | None = None) -> TranscriptResult` — НИКОГДА не бросает исключений наружу (все ошибки → поле `error`); `api_key=None` → читает `os.environ.get("OPENAI_API_KEY")`
  - Внутренние швы для тестов (подменяются через monkeypatch): `_extract_info(url) -> dict`, `_fetch_captions(url) -> str`, `_transcribe_via_whisper(url, lang, api_key) -> tuple[list[Segment], str | None]`
  - `source ∈ {"manual", "auto", "whisper", None}`

- [ ] **Step 1: Написать падающие тесты**

`tests/test_transcription.py`:
```python
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
```

Примечание: последний тест лишь проверяет, что playlist-URL обрабатывается через тот же путь (реальный разворот плейлиста происходит внутри `_extract_info`, который здесь замокан) — он страхует, что `get_transcript` не завязан на форму URL.

- [ ] **Step 2: Запустить и убедиться, что падает**

Run: `.venv/bin/python -m pytest tests/test_transcription.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.transcription'`

- [ ] **Step 3: Реализовать `app/transcription.py`**

```python
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


def _pick_language(keys: list, want: str | None) -> str | None:
    if not keys:
        return None
    if want:
        if want in keys:
            return want
        base = want.split("-")[0]
        for key in keys:
            if key.split("-")[0] == base:
                return key
        return None
    for pref in DEFAULT_LANG_PREFERENCE:
        if pref in keys:
            return pref
        for key in keys:
            if key.split("-")[0] == pref:
                return key
    return sorted(keys)[0]


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
        lang_key = _pick_language(list(table), want)
        if not lang_key:
            continue
        fmt = _pick_format(table[lang_key])
        if not fmt:
            continue
        try:
            raw = _fetch_captions(fmt["url"])
        except Exception as exc:
            logger.warning("caption fetch failed for %s: %s", url, exc)
            continue
        segments = parse_vtt(raw) if fmt["ext"] == "vtt" else parse_srt(raw)
        if not segments:
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


def get_transcript(
    url: str, lang: str | None = None, api_key: str | None = None
) -> TranscriptResult:
    if api_key is None:
        api_key = os.environ.get("OPENAI_API_KEY") or None
    video_id = cache.cache_key(url)

    hit = cache.load(video_id)
    if hit:
        hit_result = TranscriptResult.from_cache(hit)
        if hit_result.source == "whisper" or lang is None or hit_result.language == lang:
            return hit_result

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
        cache.save(video_id, result.to_cache())
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
    cache.save(video_id, result.to_cache())
    return result
```

- [ ] **Step 4: Запустить тесты**

Run: `.venv/bin/python -m pytest tests/test_transcription.py -v`
Expected: 11 passed

- [ ] **Step 5: Прогнать весь набор**

Run: `.venv/bin/python -m pytest -v`
Expected: все passed (scaffold + vtt + cache + whisper + transcription)

- [ ] **Step 6: Коммит**

```bash
git add app/transcription.py tests/test_transcription.py
git commit -m "feat: transcription chain manual->auto->whisper with cache"
```

---

### Task 6: Пакетная обработка (job'ы с прогрессом)

**Files:**
- Create: `app/batch.py`
- Test: `tests/test_batch.py`

**Interfaces:**
- Consumes: `app.transcription.get_transcript(url, lang=None)` — вызов строго через `transcription.get_transcript(...)` (тесты подменяют атрибут модуля)
- Produces (использует Task 7):
  - `MAX_WORKERS = 5`
  - `class JobStore(workers: int = MAX_WORKERS)`
    - `create(urls: list[str], lang: str | None = None) -> str` — возвращает `job_id`
    - `get(job_id: str) -> dict | None` — `{"job_id", "status": "running"|"done", "items": [{"url", "status", "video_id", "title", "source", "language", "error"}]}`
    - `item.status ∈ {"pending", "running", "done", "error"}`
  - Исключение внутри обработчика одной ссылки → `status="error"`, `error="внутренняя ошибка: ..."`, остальные ссылки не затрагиваются
  - `TranscriptResult.error` непуст → `status="error"` с этим же текстом

- [ ] **Step 1: Написать падающие тесты**

`tests/test_batch.py`:
```python
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
```

- [ ] **Step 2: Запустить и убедиться, что падает**

Run: `.venv/bin/python -m pytest tests/test_batch.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.batch'`

- [ ] **Step 3: Реализовать `app/batch.py`**

```python
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
```

- [ ] **Step 4: Запустить тесты**

Run: `.venv/bin/python -m pytest tests/test_batch.py -v`
Expected: 5 passed

- [ ] **Step 5: Коммит**

```bash
git add app/batch.py tests/test_batch.py
git commit -m "feat: batch job store with parallel workers and per-item status"
```

---

### Task 7: FastAPI — роуты, статика, скачивание

**Files:**
- Create: `app/main.py`
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: `app.batch.JobStore`, `app.cache`, `app.transcription.get_transcript`, `app.vtt.segments_to_srt/segments_to_text`
- Produces (использует Task 8 — фронтенд ходит ровно в эти URL):
  - `GET /` → HTML из `static/index.html`
  - `POST /api/parse` body `{"links": [str], "lang": str | null}` → `200 {"job_id": "..."}`; 0 или >50 ссылок → `422`; не-http(s)-ссылка → `400 {"detail": ...}`
  - `GET /api/jobs/{job_id}` → схема из Task 6; неизвестный → `404`
  - `GET /api/transcript/{video_id}?lang=ru` → кэш-JSON (`url, video_id, title, source, language, available_languages, segments[]`); невалидный id → `400`; нет файла → `404`; при `?lang` ≠ закэшированного и source ≠ whisper — рефетч `get_transcript` по `url` из кэша
  - `GET /api/transcript/{video_id}/download?format=srt|txt` → `text/plain` c `Content-Disposition: attachment`; невалидный `format` → `400`
  - `GET /api/jobs/{job_id}/zip` → `application/zip`, только `done`-элементы (`{title|video_id}.srt`); нет готовых → `404`
  - `app` — объект FastAPI, точка входа `app.main:app`
  - `store` — модульный singleton `JobStore` (тесты работают с ним как есть; каждый TestClient-тест создаёт свежий job)

- [ ] **Step 1: Написать падающие тесты**

`tests/test_api.py`:
```python
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
```

- [ ] **Step 2: Запустить и убедиться, что падает**

Run: `.venv/bin/python -m pytest tests/test_api.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.main'`

- [ ] **Step 3: Реализовать `app/main.py`**

```python
from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response
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
    cleaned = re.sub(r'[\\/:*?"<>|]+', "_", title).strip(" .")
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
    return Response(
        body,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
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
```

- [ ] **Step 4: Запустить тесты**

Run: `.venv/bin/python -m pytest tests/test_api.py -v`
Expected: 7 passed

- [ ] **Step 5: Прогнать весь набор**

Run: `.venv/bin/python -m pytest -v`
Expected: все passed

- [ ] **Step 6: Коммит**

```bash
git add app/main.py tests/test_api.py
git commit -m "feat: fastapi routes for parse, jobs, transcript download and zip"
```

---

### Task 8: Фронтенд — одна HTML-страница

**Files:**
- Create: `static/index.html`

**Interfaces:**
- Consumes (все URL из Task 7):
  - `POST /api/parse` → `{"job_id"}`
  - `GET /api/jobs/{id}` → `{"status", "items": [{"url","status","video_id","title","source","language","error"}]}`
  - `GET /api/transcript/{video_id}?lang=` → кэш-JSON
  - `GET /api/transcript/{video_id}/download?format=srt|txt`
  - `GET /api/jobs/{id}/zip`
- Produces: визуальный результат для ручной проверки в Task 9. Юнит-тестов нет (ванильный JS без сборки) — проверяется в Task 9 чек-листом.

Поведение страницы:
- Многострочное поле для ссылок (одна в строке) + необязательное поле языка + кнопка «Получить транскрипты».
- После отправки — таблица: ссылка, статус (pending/running/done/error + текст ошибки), название, источник (manual/auto/whisper), кнопка «Смотреть».
- Поллинг `GET /api/jobs/{id}` каждые 700 мс до `status === "done"`; по мере готовности строк кнопки активируются.
- «Смотреть» → `GET /api/transcript/{video_id}` → блок просмотра: заголовок, чипы доступных языков (клик = повторный запрос с `?lang=`), текст `<pre>` с таймкодами, кнопки «Скачать .srt», «Скачать .txt».
- Кнопка «Скачать всё ZIP» → переход на `/api/jobs/{id}/zip` (появляется когда есть хоть один done).
- Ошибка сети при поллинге → сообщение, не бесконечный спиннер.

- [ ] **Step 1: Создать `static/index.html`**

```html
<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Парсер транскриптов</title>
<style>
  :root { color-scheme: light dark; }
  body { font-family: system-ui, sans-serif; max-width: 960px; margin: 2rem auto; padding: 0 1rem; }
  h1 { font-size: 1.4rem; }
  textarea { width: 100%; font: inherit; padding: .5rem; box-sizing: border-box; }
  .row { display: flex; gap: .5rem; margin: .75rem 0; align-items: center; flex-wrap: wrap; }
  input[type=text] { padding: .4rem; font: inherit; }
  button { padding: .45rem .9rem; font: inherit; cursor: pointer; }
  button:disabled { opacity: .5; cursor: default; }
  table { width: 100%; border-collapse: collapse; margin-top: 1rem; }
  th, td { text-align: left; padding: .4rem .5rem; border-bottom: 1px solid #8884; vertical-align: top; }
  .st-done { color: #1a7f37; }
  .st-error { color: #cf222e; }
  .st-running, .st-pending { color: #9a6700; }
  .err-text { font-size: .85rem; color: #cf222e; }
  #viewer { position: fixed; inset: 0; background: Canvas; overflow: auto; padding: 1.5rem; }
  #viewer pre { white-space: pre-wrap; background: #8881; padding: 1rem; border-radius: 6px; }
  .chip { border: 1px solid #8886; border-radius: 999px; padding: .15rem .6rem; cursor: pointer; font-size: .85rem; }
  .chip.active { background: #0969da; color: white; border-color: transparent; }
  a.dl { margin-right: .75rem; }
</style>
</head>
<body>
<h1>Парсер транскриптов</h1>
<p>Вставьте ссылки на видео (одна в строке). Источники: авторские субтитры → автосубтитры → Whisper API.</p>

<textarea id="links" rows="6" placeholder="https://www.youtube.com/watch?v=...&#10;https://youtu.be/..."></textarea>

<div class="row">
  <input type="text" id="lang" placeholder="язык (напр. ru) — пусто = авто" size="24">
  <button id="go">Получить транскрипты</button>
  <a id="zip" style="display:none">Скачать всё ZIP</a>
</div>
<p id="msg" class="err-text"></p>

<table id="tbl" hidden>
  <thead>
    <tr><th>Ссылка</th><th>Статус</th><th>Название</th><th>Источник</th><th></th></tr>
  </thead>
  <tbody id="rows"></tbody>
</table>

<div id="viewer" hidden>
  <div class="row">
    <strong id="v-title"></strong>
    <span id="v-langs"></span>
  </div>
  <div class="row">
    <a class="dl" id="dl-srt">Скачать .srt</a>
    <a class="dl" id="dl-txt">Скачать .txt</a>
    <button id="close">Закрыть</button>
  </div>
  <pre id="v-text"></pre>
</div>

<script>
"use strict";
const $ = (id) => document.getElementById(id);
const SRC_LABEL = { manual: "ручные субтитры", auto: "автосубтитры", whisper: "Whisper" };
let jobId = null;
let pollTimer = null;
let currentVideoId = null;
let availableLangs = [];
let currentLang = null;

async function api(path, options) {
  const resp = await fetch(path, options);
  if (!resp.ok) {
    let detail = resp.statusText;
    try { detail = (await resp.json()).detail || detail; } catch (e) {}
    throw new Error(detail);
  }
  return resp;
}

function statusLabel(item) {
  if (item.status === "pending") return "в очереди";
  if (item.status === "running") return "обрабатывается…";
  if (item.status === "done") return "готово";
  return item.error || "ошибка";
}

function renderRows(items) {
  const tbody = $("rows");
  tbody.innerHTML = "";
  for (const item of items) {
    const tr = document.createElement("tr");

    const tdUrl = document.createElement("td");
    tdUrl.textContent = item.url;
    tr.appendChild(tdUrl);

    const tdStatus = document.createElement("td");
    tdStatus.className = "st-" + item.status;
    tdStatus.textContent = statusLabel(item);
    tr.appendChild(tdStatus);

    const tdTitle = document.createElement("td");
    tdTitle.textContent = item.title || "";
    tr.appendChild(tdTitle);

    const tdSource = document.createElement("td");
    tdSource.textContent = item.source ? (SRC_LABEL[item.source] || item.source) : "";
    tr.appendChild(tdSource);

    const tdAction = document.createElement("td");
    if (item.status === "done" && item.video_id) {
      const btn = document.createElement("button");
      btn.textContent = "Смотреть";
      btn.onclick = () => openTranscript(item.video_id, null);
      tdAction.appendChild(btn);
    }
    tr.appendChild(tdAction);

    tbody.appendChild(tr);
  }
  const anyDone = items.some((i) => i.status === "done");
  const zip = $("zip");
  zip.style.display = anyDone ? "inline" : "none";
  if (jobId) zip.href = "/api/jobs/" + jobId + "/zip";
}

async function poll() {
  try {
    const job = await (await api("/api/jobs/" + jobId)).json();
    renderRows(job.items);
    if (job.status === "done") {
      clearTimeout(pollTimer);
      $("go").disabled = false;
      $("msg").textContent = "";
    } else {
      pollTimer = setTimeout(poll, 700);
    }
  } catch (err) {
    $("msg").textContent = "Ошибка опроса: " + err.message + ". Обновите страницу.";
    $("go").disabled = false;
  }
}

async function start() {
  const lines = $("links").value.split("\n").map((s) => s.trim()).filter(Boolean);
  if (!lines.length) { $("msg").textContent = "Вставьте хотя бы одну ссылку."; return; }
  const lang = $("lang").value.trim() || null;
  $("msg").textContent = "";
  $("go").disabled = true;
  $("zip").style.display = "none";
  clearTimeout(pollTimer);
  try {
    const resp = await api("/api/parse", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ links: lines, lang: lang }),
    });
    const data = await resp.json();
    jobId = data.job_id;
    $("tbl").hidden = false;
    renderRows(lines.map((u) => ({ url: u, status: "pending" })));
    pollTimer = setTimeout(poll, 300);
  } catch (err) {
    $("msg").textContent = err.message;
    $("go").disabled = false;
  }
}

async function openTranscript(videoId, lang) {
  try {
    const url = "/api/transcript/" + encodeURIComponent(videoId) +
      (lang ? "?lang=" + encodeURIComponent(lang) : "");
    const data = await (await api(url)).json();
    currentVideoId = videoId;
    currentLang = lang || data.language;
    availableLangs = data.available_languages || [];
    $("v-title").textContent = data.title || videoId;
    $("v-text").textContent = (data.segments || [])
      .map((s) => {
        const t = Math.floor(s.start);
        const mm = String(Math.floor(t / 60)).padStart(2, "0");
        const ss = String(t % 60).padStart(2, "0");
        return "[" + mm + ":" + ss + "] " + s.text;
      }).join("\n") || "(пусто)";
    $("dl-srt").href = "/api/transcript/" + videoId + "/download?format=srt";
    $("dl-txt").href = "/api/transcript/" + videoId + "/download?format=txt";
    $("dl-srt").setAttribute("download", "");
    $("dl-txt").setAttribute("download", "");
    const langs = $("v-langs");
    langs.innerHTML = "";
    if (availableLangs.length > 1) {
      for (const code of availableLangs) {
        const chip = document.createElement("span");
        chip.className = "chip" + (code === currentLang ? " active" : "");
        chip.textContent = code;
        chip.onclick = () => openTranscript(videoId, code);
        langs.appendChild(chip);
      }
    }
    $("viewer").hidden = false;
  } catch (err) {
    $("msg").textContent = "Не удалось показать транскрипт: " + err.message;
  }
}

$("go").onclick = start;
$("close").onclick = () => { $("viewer").hidden = true; };
</script>
</body>
</html>
```

- [ ] **Step 2: Проверить, что сервер отдаёт страницу**

```bash
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 &
sleep 2
curl -s http://127.0.0.1:8000/ | head -5
kill %1
```

Expected: первые строки HTML (`<!DOCTYPE html>`, `<html lang="ru">`, заголовок «Парсер транскриптов»).

- [ ] **Step 3: Коммит**

```bash
git add static/index.html
git commit -m "feat: single-page ui with job polling and transcript viewer"
```

---

### Task 9: Ручная проверка E2E и README

**Files:**
- Create: `README.md`
- Не создаёт файлов кода — верификация работы целого приложения.

**Interfaces:**
- Consumes: всё из Tasks 1–8
- Produces: README с инструкцией запуска; подтверждённая работоспособность на реальных ссылках.

- [ ] **Step 1: Запустить тесты**

Run: `.venv/bin/python -m pytest -v`
Expected: все passed

- [ ] **Step 2: Запустить сервер**

```bash
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

- [ ] **Step 3: Проверить API на реальных ссылках**

В другом терминале:

```bash
# видео «Me at the zoo» — есть авторские субтитры (en, de)
curl -s -X POST http://127.0.0.1:8000/api/parse \
  -H 'Content-Type: application/json' \
  -d '{"links": ["https://www.youtube.com/watch?v=jNQXAC9IVRw"]}'
# → {"job_id": "..."}
curl -s http://127.0.0.1:8000/api/jobs/<job_id>   # дождаться status=done
curl -s http://127.0.0.1:8000/api/transcript/jNQXAC9IVRw | head -c 500
```

Expected: `status: "done"`, `source: "manual"`, segments с текстом.

Чек-лист ручной проверки в браузере (`http://127.0.0.1:8000`):

- [ ] Три ссылки вставляются разом: (а) видео с авторскими субтитрами, (б) видео только с автосубтитрами, (в) мусорная ссылка `https://example.com/notavideo`.
- [ ] Статусы обновляются сами; у (в) — «ошибка», у (а)(б) — «готово».
- [ ] Чипы языков видны (для «Me at the zoo» — en, de); клик по языку меняет текст.
- [ ] «Скачать .srt» и «Скачать .txt» отдают файлы с правильным форматом.
- [ ] «Скачать всё ZIP» содержит только готовые транскрипты.
- [ ] Повторная отправка тех же ссылок — мгновенно (кэш, `data/*.json` появились).
- [ ] Видео без субтитров: статус «нет транскриптов … OPENAI_API_KEY не задан» (без ключа) или расшифровка Whisper (с ключом в `.env`).

- [ ] **Step 4: Написать README.md**

```markdown
# Парсер транскриптов

Извлекает субтитры/транскрипты с видео (1000+ сайтов через yt-dlp):
авторские субтитры → автосубтитры → Whisper API (OpenAI) → «нет транскриптов».

## Запуск

```bash
python3 -m venv --system-site-packages .venv   # уже создан в этом репо
.venv/bin/pip install -r requirements.txt
cp .env.example .env        # вписать OPENAI_API_KEY (опционально, для Whisper)
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Открыть http://127.0.0.1:8000 — вставить ссылки, получить транскрипты,
смотреть с таймкодами, скачать .srt / .txt / ZIP.

## Тесты

```bash
.venv/bin/python -m pytest -v
```

## Как это работает

- `app/transcription.py` — цепочка источников и кэш.
- `app/vtt.py` — разбор VTT/SRT, удаление roll-up дублей автосубтитров.
- `app/whisper_client.py` — OpenAI Whisper API (только как резерв).
- `app/batch.py` — пакетная обработка до 5 ссылок параллельно.
- `data/` — кэш транскриптов (JSON), попадает в .gitignore.

Требуется `ffmpeg` в PATH (конвертация аудио перед Whisper).
```

- [ ] **Step 5: Финальный коммит**

```bash
git add README.md
git commit -m "docs: add run instructions"
```

- [ ] **Step 6: Финальная проверка**

Run: `.venv/bin/python -m pytest -v && git log --oneline`
Expected: все тесты passed; в истории 9 коммитов (scaffold, vtt, cache, whisper, transcription, batch, main, ui, docs).

---

## Self-Review (выполнен автором плана)

1. **Spec coverage:** ссылки+валидация (T7), цепочка источников (T5), Whisper ретраи (T4), кэш с video_id (T3+T5), пакетная обработка 5 параллельно (T6), показ на странице + языковые чипы (T8), .srt/.txt/ZIP (T7), обработка ошибок по строкам таблицы спецификации (T5/T6/T7), безопасность — ключ в .env, 127.0.0.1, валидация ссылок, path traversal (T1/T3/T7), тестирование по разделу Testing (T2–T7 юниты + T9 ручное). Пробелов нет.
2. **Placeholder scan:** нет TBD/TODO; каждый шаг кода содержит полный код.
3. **Type consistency:** `Segment(start,end,text)` — везде; `TranscriptResult.to_cache/from_cache` — T5/T6/T7 согласованы; схема `job.items` из `JobStore.get` повторяется в тестах T7 и фронте T8; `get_transcript(url, lang=None, api_key=None)` — в T5, T6, T7, тестах.
4. **Review Focus:** все пять пунктов привязаны к тестам в соответствующих задачах (см. раздел Review Focus).
