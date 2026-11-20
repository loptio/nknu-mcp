"""11-18 (爬虫judge: the status file's export.owed listed 33 sites — the five JS sites, round two's off-campus units,
github.com, www.ted.com… — all exported by day and in current.tsv, owed only because a night never exports them; the
dashboard's and the panels' 「欠导出」 should be only what is really owed): a day's run over a round-two site puts it in
the night's change list, the night's export takes only the sites under periodic update (export_list's `only`), so such
a site joined the owed list and never left it. Owed now means owed by the night: a site not under periodic update is
never owed, and one owed from before is let go at the next review or seen. When the update sites cannot be read
nothing is dropped (as before). 11-19: the list's revisit sites (the off-campus unit sites, github.com among them)
are the night's too, on their night (test_revisit_1119): the sites here that the night never takes are made up."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nknu.archive import crawler as crawl_mod
from nknu.archive import update
from nknu.archive.frontier import Frontier
from tests.archive.test_nightly_export_1116 import _status

UPDATE = ["a.nknu.edu.tw", "c.nknu.edu.tw"]


def _under_update(root: Path, keys: list[str]) -> None:
    f = Frontier(root / "frontier.db")
    try:
        f.meta(crawl_mod.UPDATE_SITES_KEY, json.dumps(keys))
    finally:
        f.close()


@pytest.fixture
def root(tmp_path):
    Frontier(tmp_path / "frontier.db").close()
    _status(tmp_path, sites_changed=["a.nknu.edu.tw", "js.nknu.edu.tw"], alarms={})
    return tmp_path


def test_a_site_a_day_s_run_changed_is_not_owed_by_the_night(root):
    _under_update(root, UPDATE)
    update.owe(root, ["a.nknu.edu.tw", "js.nknu.edu.tw"])  # the review: js was fetched by a day's run
    assert update._owed(root) == ["a.nknu.edu.tw"]


def test_a_site_owed_from_before_and_not_under_update_is_let_go_at_the_next_review(root):
    update._write_owed(root, ["c.nknu.edu.tw", "club.example.org"])
    _under_update(root, UPDATE)
    update.owe(root, [])
    assert update._owed(root) == ["c.nknu.edu.tw"]


def test_seen_lets_go_of_sites_the_night_never_exports(root):
    update._write_owed(root, ["a.nknu.edu.tw", "c.nknu.edu.tw", "club.example.org", "w3.nknu.edu.tw"])
    _under_update(root, UPDATE)
    results = root / "results.tsv"
    results.write_text("c.nknu.edu.tw\tok\n", encoding="utf-8")
    assert update.main(["--dir", str(root), "seen", "--results", str(results), "--out", str(root / "seen.tsv")]) == 0
    assert update._owed(root) == ["a.nknu.edu.tw"]
    status = json.loads((root / "reports" / update.STATUS).read_text(encoding="utf-8"))
    assert status["export"]["owed"] == ["a.nknu.edu.tw"]


def test_without_the_update_sites_nothing_owed_is_dropped(root):
    update._write_owed(root, ["club.example.org"])
    update.owe(root, ["js.nknu.edu.tw"])  # no meta update:sites: no filter, as before
    assert update._owed(root) == ["club.example.org", "js.nknu.edu.tw"]
