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
