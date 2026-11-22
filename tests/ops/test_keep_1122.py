"""What the background work found survives a restart (2026-11-22 night; owner: 「不应该是在资料库吗？」): nknu.ops.keep
in ops.db, read back by the same key, ignored for another."""

from __future__ import annotations

from pathlib import Path

from nknu.ops import keep, overview, pipeline


def test_a_value_comes_back_only_for_its_key(tmp_path):
    kept = keep.Kept(tmp_path / "ops.db")
    kept.put("sites", ("a", 1, "2026-11-22"), {"n": 1})
    again = keep.Kept(tmp_path / "ops.db")  # a new process over the same file
    assert again.get("sites", ("a", 1, "2026-11-22")) == {"n": 1}
    assert again.get("sites", ("a", 2, "2026-11-22")) is None and again.get("other", "x") is None
    assert keep.Kept(None).get("sites", "x") is None  # nothing kept without the file: never a failure
    keep.Kept(None).put("sites", "x", {"n": 1})


def test_the_steps_read_before_a_restart_are_shown_until_a_new_reading_ends(tmp_path):
    kept = keep.Kept(tmp_path / "ops.db")
    kept.put("pipeline", "last", {"at": "2026-11-22T21:00:00+08:00", "steps": [{"key": "crawl"}], "sites": []})
    where = pipeline.Where(root=tmp_path, db=tmp_path / "aca.db")
    flow = pipeline.Pipeline(where, probes=[], kept=kept)
    got = flow.snapshot()
    assert got["kept"] and got["steps"] == [{"key": "crawl"}]
    assert flow.last() is None  # the nights' record never takes the reading of another process
    flow.wait(5)
    assert not flow.snapshot().get("kept")  # the new reading replaces it


def test_the_sites_data_of_the_same_files_is_not_counted_again(tmp_path, monkeypatch):
    kept = keep.Kept(tmp_path / "ops.db")
    sig = ("files", "2026-11-22")
    monkeypatch.setattr(overview, "signature", lambda root, day: sig)
    kept.put("sites", sig, {"sites": [{"k": "a"}], "built": "2026-11-22 03:01", "stamp": "s1", "live": True})
    calls = []
    monkeypatch.setattr(overview.overview_data, "collect", lambda *a, **k: calls.append(1) or {})
    state = overview.State(Path(tmp_path), template="<main class=\"app\">__DATA__ __LINKS__</main>", kept=kept)
    state.tick()
    assert state.data["sites"] == [{"k": "a"}] and not calls and state.page
