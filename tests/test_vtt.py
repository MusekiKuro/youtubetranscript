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
