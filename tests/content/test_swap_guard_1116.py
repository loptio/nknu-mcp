"""spec 2026-11-16 §五 (plan 2026-11-16 T5): a rebuild does not put itself in the place of the library in use when a
site it built holds fewer than half the documents the library in use holds for it (GUARD_MIN or more there): a crawl
that failed, an export folder that is half empty. The new library stays beside the old one (<name>.held.db), the old
one is untouched, and --force-swap puts it in. The same thought as 「抓取失败或网站改版时，保留旧值」. A site the new
build does not build at all is no collapse (a trial of a few sites), nor is anything checked against a file that is
no library of this shape."""
from __future__ import annotations

import sqlite3

import pytest

from nknu.content import build
from tests.content import test_copies_1114 as tc
from tests.content import test_multisite_1108 as tm
from tests.content.test_validity_1114 import ACA, ACA_SITE, PDF


def site(tmp_path, name: str, n: int, key: str = ACA_SITE, base: str = ACA):
    rows = [{"kind": PDF, "url": f"{base}f/{i}/表{i}.pdf", "title": f"第{i}號表.pdf", "text": f"第 {i} 號表的內容。"}
            for i in range(n)]
    return tc.site_input(tmp_path / name, key, "教务处" if key == ACA_SITE else "通識中心", rows)


def count(path) -> dict:
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
        return dict(conn.execute("SELECT site, COUNT(*) FROM docs GROUP BY site"))


@pytest.fixture
def lib(tmp_path):
    paths, out = tm.tree_files(tmp_path / "tree"), tmp_path / "db" / "x.db"
    tm.run(paths, out, inputs=[site(tmp_path, "full", 25)])
    assert count(out) == {ACA_SITE: 25} and build.GUARD_MIN == 20
    return paths, out


def test_a_site_fallen_below_half_holds_the_new_library_back(tmp_path, lib):
    paths, out = lib
    with pytest.raises(build.SwapHeld) as held:
        tm.run(paths, out, inputs=[site(tmp_path, "thin", 12)])
    assert held.value.sites == [(ACA_SITE, 25, 12)] and held.value.held == out.with_name("x.held.db")
    assert count(out) == {ACA_SITE: 25} and count(held.value.held) == {ACA_SITE: 12}
    assert "没有换上" in str(held.value) and ACA_SITE in str(held.value) and "--force-swap" in str(held.value)
    assert not out.with_name("x.db.tmp").exists()


def test_half_or_more_is_swapped_in_and_so_is_a_forced_one(tmp_path, lib):
    paths, out = lib
    tm.run(paths, out, inputs=[site(tmp_path, "half", 13)])
    assert count(out) == {ACA_SITE: 13}  # 13 x 2 >= 25; and a library of 13 is under GUARD_MIN: nothing to guard
    tm.run(paths, out, inputs=[site(tmp_path, "few", 2)])
    assert count(out) == {ACA_SITE: 2}
    tm.run(paths, out, inputs=[site(tmp_path, "full2", 25)])
    tm.run(paths, out, inputs=[site(tmp_path, "thin", 3)], force_swap=True)
    assert count(out) == {ACA_SITE: 3}


def test_a_site_the_build_leaves_out_is_no_collapse(tmp_path, lib):
    paths, out = lib
    other = site(tmp_path, "tg", 4, tc.TG_SITE, tc.TG)
    # this guard's own rule; since 11-17 the departure guard holds such a build (tests/content/test_departures_1117.py:
    # the documents of a site the build was not given left for no reason), so it is switched off here
    tm.run(paths, out, inputs=[other], departure_guard=False)
    assert count(out) == {tc.TG_SITE: 4}


def test_a_file_that_is_no_library_of_this_shape_is_replaced(tmp_path):
    paths = tm.tree_files(tmp_path / "tree")
    for name, make in (("old.db", "CREATE TABLE docs (doc_id TEXT)"), ("junk.db", None)):
        out = tmp_path / "db" / name
        out.parent.mkdir(exist_ok=True)
        if make:
            with sqlite3.connect(out) as conn:
                conn.execute(make)
        else:
            out.write_bytes(b"not a database")
        tm.run(paths, out, inputs=[site(tmp_path, "in-" + name, 3)])
        assert count(out) == {ACA_SITE: 3}


def test_the_command_says_why_and_ends_with_3(tmp_path, lib, capsys):
    paths, out = lib
    corpus, export = site(tmp_path, "thin", 5)
    args = ["--input", f"{corpus}::{export}", "--out", str(out), "--aliases", str(paths["aliases"]),
            "--site-list", str(paths["sites"]), "--units", str(paths["units"])]
    assert build.main(args) == 3
    err = capsys.readouterr().err
    assert "没有换上" in err and "x.held.db" in err and count(out) == {ACA_SITE: 25}
    assert build.main([*args, "--force-swap"]) == 0 and count(out) == {ACA_SITE: 5}
