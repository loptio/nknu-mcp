"""The to-text cache (plan 2026-11-02 T3b): one folder per content hash, result.json written last and valid only at
the current pipeline version, a 'started' marker while a file converts."""

from __future__ import annotations

import hashlib

import pytest

from nknu.totext.cache import PIPELINE_VERSION, Cache, sha256_file, write_atomic

SHA = hashlib.sha256(b"one").hexdigest()


def _names(folder):
    return sorted(p.name for p in folder.iterdir())


def test_sha256_file(tmp_path):
    p = tmp_path / "f.bin"
    p.write_bytes(b"one")
    assert sha256_file(p) == SHA


def test_save_is_atomic_and_versioned(tmp_path):
    cache = Cache(tmp_path / "cache")
    assert cache.dir(SHA) == tmp_path / "cache" / SHA[:2] / SHA
    assert cache.done(SHA) is None and not cache.crashed(SHA) and cache.text(SHA) == ""
    cache.begin(SHA)
    assert cache.crashed(SHA) and cache.crash_note(SHA) == ""
    cache.save(SHA, {"status": "ok"}, "文字", {"ocr.json": b"[]", "docling.json.gz": b"x"})
    assert cache.done(SHA) == {"version": PIPELINE_VERSION, "status": "ok"}
    assert cache.text(SHA) == "文字" and not cache.crashed(SHA)
    assert _names(cache.dir(SHA)) == ["docling.json.gz", "ocr.json", "result.json", "text.md"]  # no marker, no *.tmp
    cache.save(SHA, {"version": "totext-0", "status": "ok"}, "舊", {"ocr.json": b"[1]"})
    assert cache.done(SHA) is None and not cache.crashed(SHA)  # another version: not done, and no crash either
    assert _names(cache.dir(SHA)) == ["ocr.json", "result.json", "text.md"]  # the earlier save's extra is gone


def test_results_of_earlier_conversions_are_converted_again(tmp_path):
    # review of phase 1: T4 and T5 changed what the pdf, image and archive routes produce (images were skipped with
    # 「OCR 还没接上」, archives with 「压缩档还没解开」, mixed PDFs had no region OCR) and the review's fixes change it
    # again; a cache written before them must not count as done
    cache = Cache(tmp_path / "cache")
    cache.save(SHA, {"version": "totext-1", "status": "skipped", "reason": "OCR 还没接上"}, "", {})
    assert PIPELINE_VERSION == "totext-2" and cache.done(SHA) is None


def test_crash_note_and_abandon(tmp_path):
    cache = Cache(tmp_path / "cache")
    cache.begin(SHA, note="转换超过 9 秒没有进展")
    assert cache.crashed(SHA) and cache.crash_note(SHA) == "转换超过 9 秒没有进展"
    cache.abandon(SHA)
    assert not cache.crashed(SHA) and cache.crash_note(SHA) == ""


def test_marker_beside_a_current_result_is_no_crash(tmp_path):
    # the process died after result.json and before the marker was dropped: the file is done
    cache = Cache(tmp_path / "cache")
    cache.save(SHA, {"status": "ok"}, "", {})
    cache.begin(SHA)
    assert cache.done(SHA) is not None and not cache.crashed(SHA)


def test_damaged_result_is_not_done(tmp_path):
    cache = Cache(tmp_path / "cache")
    cache.begin(SHA)
    (cache.dir(SHA) / "result.json").write_text("{not json", encoding="utf-8")
    assert cache.done(SHA) is None and cache.crashed(SHA)


def test_keys_are_sha256_only(tmp_path):
    cache = Cache(tmp_path / "cache")
    for bad in ("../x", "x", SHA.upper(), SHA + "0"):
        with pytest.raises(ValueError):
            cache.dir(bad)
    with pytest.raises(ValueError):
        cache.save(SHA, {}, "", {"../evil": b""})
    with pytest.raises(ValueError):
        cache.save(SHA, {}, "", {"result.json": b"{}"})


def test_write_atomic_leaves_no_temporary_file(tmp_path):
    target = tmp_path / "a.txt"
    write_atomic(target, b"one")
    write_atomic(target, b"two")
    assert target.read_bytes() == b"two" and _names(tmp_path) == ["a.txt"]
