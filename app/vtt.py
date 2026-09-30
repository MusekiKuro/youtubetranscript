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


def _normalize(text: str) -> str:
    return " ".join(text.split())


_ECHO_MAX_DURATION = 0.05


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
        while i < len(lines) and not _TIMING_RE.search(lines[i]):
            if not lines[i].strip():
                # Пустая строка — конец cue; строка из пробелов — пропускаем
                # (YouTube ASR ставит " " до контекста и после эхо-cue).
                if lines[i] == "" or block:
                    break
                i += 1
                continue
            cleaned = _clean(lines[i])
            if cleaned:
                block.append(cleaned)
            i += 1
        full = " ".join(block)
        if not full:
            continue
        text = _strip_rollup(full, previous_full)
        previous_full = full
        if (
            segments
            and end - start <= _ECHO_MAX_DURATION
            and _normalize(text) == _normalize(segments[-1].text)
        ):
            # YouTube ASR добавляет 10мс echo-cue, повторяющую только что
            # выданную реплику, — дубль, а не настоящее повторение речи.
            continue
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
