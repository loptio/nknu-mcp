"""Round two's batch two: the promote action (design docs/superpowers/specs/2026-11-17-round2-batch2-design.md §三.2,
§四). `python -m nknu.archive.update round2 --sites …` judges again each `later` row of the named sites by the list as
it is now: one the list places this round goes pending, its tier, leaf and (for a row only an old source named) depth
set as this round's, under the site's caps (its own, else a round-one site's); what it was before is kept, so --undo
puts back every row nothing has fetched since; --dry-run counts it all and changes nothing. While a batch is open its
sites under the nightly update are held by a round-one site's caps, not the nightly growth caps. Made-up sites."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nknu.archive import batch, round1, update
from nknu.archive.frontier import Frontier

BATCH = "2026-11-17"
SITES = [
    {"site": "ks.nknu.edu.tw", "round": 1, "imp": "全校重要", "home": "https://ks.nknu.edu.tw/"},
    {"site": "u.example.org", "round": 2, "why": "offsite_unit", "home": "https://u.example.org/", "now": BATCH,
     "caps": {"page": 2, "file": 5, "image": 5}},
    {"site": "github.com", "round": 2, "why": "offsite_unit", "home": "https://github.com/unit", "paths": ["/unit"],
     "now": BATCH, "caps": {"page": 50, "file": 50, "image": 50}},
    {"site": "low.example.org", "round": 2, "why": "offsite_unit", "home": "https://low.example.org/"},
]
PLAN = round1.Plan({s["site"]: round1.parse_site(s) for s in SITES}, (), (), "")


def _row(f: Frontier, url: str, owner: str, **cols) -> int:
    host = url.split("/")[2]
    row = {"url": url, "path": url.split("?", 1)[0], "host": host, "site": owner, "owner_site": owner, "tier": 2,
           "kind": "page", "kind0": "page", "depth": 3, "found_via": "link", "state": "later",
           "reason": "round2:other_site", "leaf": 0}
    row.update(cols)
    with f.lock:
        return f.conn.execute(f"INSERT INTO url({', '.join(row)}) VALUES ({','.join('?' * len(row))})",
                              tuple(row.values())).lastrowid


@pytest.fixture
def ledger(tmp_path):
    f = Frontier(tmp_path / "frontier.db")
    ids = {
        "old": _row(f, "https://ks.nknu.edu.tw/news?id=1", "ks.nknu.edu.tw", found_via="old_frontier", depth=15),
        "link": _row(f, "https://ks.nknu.edu.tw/news?id=2", "ks.nknu.edu.tw"),
        "file": _row(f, "https://ks.nknu.edu.tw/f.pdf", "ks.nknu.edu.tw", kind="file", kind0="file", leaf=1),
        "done2": _row(f, "https://ks.nknu.edu.tw/about", "ks.nknu.edu.tw", state="done", reason=None, status=200,
                      fetched_at="2026-11-08T00:00:00+00:00"),
        "u1": _row(f, "https://u.example.org/a", "u.example.org", reason="round2:offsite_unit"),
        "u2": _row(f, "https://u.example.org/b", "u.example.org", reason="round2:offsite_unit"),
        "u3": _row(f, "https://u.example.org/c", "u.example.org", reason="round2:offsite_unit"),
        "gh": _row(f, "https://github.com/unit/repo", "github.com", reason="round2:offsite_unit"),
        "gh_other": _row(f, "https://github.com/someone/x", "github.com", reason="round2:offsite", leaf=1),
        "low": _row(f, "https://low.example.org/a", "low.example.org", reason="round2:offsite_unit"),
    }
    yield f, ids
    f.close()


def _state(f, uid):
    r = f.row_by_id(uid)
    return r["state"], r["reason"], r["tier"], r["leaf"], r["depth"]


def test_promote_makes_this_rounds_rows_pending_under_the_caps(ledger):
    f, ids = ledger
    got = batch.promote(f, PLAN, ["ks.nknu.edu.tw", "u.example.org", "github.com"], write=True)
    assert _state(f, ids["old"])[:3] == ("pending", "round2:other_site", 0) and _state(f, ids["old"])[4] == 1
    assert _state(f, ids["link"])[4] == 3  # a row a page linked keeps its depth
    assert _state(f, ids["file"])[:4] == ("pending", "round2:other_site", 0, 1)
    assert f.row_by_id(ids["done2"])["tier"] == 0 and f.row_by_id(ids["done2"])["state"] == "done"
    states = [_state(f, ids[k])[0] for k in ("u1", "u2", "u3")]
    assert states == ["pending", "pending", "held"] and f.row_by_id(ids["u3"])["reason"] == "cap:site"
    assert _state(f, ids["gh"])[:3] == ("pending", "round2:offsite_unit", 1)
    assert _state(f, ids["gh_other"])[0] == "later"  # the same host, another's page: not the unit's
    assert got["ks.nknu.edu.tw"].promoted == {"page": 2, "file": 1} and got["ks.nknu.edu.tw"].retiered == 1
    assert got["ks.nknu.edu.tw"].rebased == 1
    assert got["u.example.org"].held == {"cap:site": 1} and got["github.com"].left == 1
    assert batch.open_sites(f) == {"ks.nknu.edu.tw", "u.example.org", "github.com"}


def test_a_site_not_in_this_batch_is_refused(ledger):
    f, _ids = ledger
    with pytest.raises(ValueError):
        batch.promote(f, PLAN, ["low.example.org"], write=True)


def test_a_dry_run_counts_the_same_and_changes_nothing(ledger):
    f, ids = ledger
    got = batch.promote(f, PLAN, ["ks.nknu.edu.tw", "u.example.org"], write=False)
    assert got["u.example.org"].promoted == {"page": 2} and got["u.example.org"].held == {"cap:site": 1}
    assert all(_state(f, ids[k])[0] == "later" for k in ("old", "link", "u1", "u3"))
    assert f.row_by_id(ids["done2"])["tier"] == 2 and batch.open_sites(f) == set()


def test_undo_puts_back_what_nothing_fetched_since(ledger):
    f, ids = ledger
    before = {k: _state(f, uid) for k, uid in ids.items()}
    batch.promote(f, PLAN, ["ks.nknu.edu.tw", "u.example.org"], write=True)
    f.x("UPDATE url SET state='done', reason=NULL, fetched_at='2026-11-17T09:00:00+00:00' WHERE id=?", (ids["link"],))
    back = batch.undo(f, ["ks.nknu.edu.tw", "u.example.org"], write=True)
    assert back == {"ks.nknu.edu.tw": 3, "u.example.org": 3}
    for k in ("old", "file", "done2", "u1", "u2", "u3"):
        assert _state(f, ids[k]) == before[k], k
    assert _state(f, ids["link"])[0] == "done"  # fetched since: kept
    assert batch.open_sites(f) == set()


def test_close_ends_the_batch_for_its_sites(ledger):
    f, _ids = ledger
    batch.promote(f, PLAN, ["ks.nknu.edu.tw", "u.example.org"], write=True)
    batch.close(f, ["u.example.org"])
    assert batch.open_sites(f) == {"ks.nknu.edu.tw"}
    batch.close(f, None)
    assert batch.open_sites(f) == set()


def test_promoting_twice_changes_nothing_more(ledger):
    f, _ids = ledger
    batch.promote(f, PLAN, ["u.example.org"], write=True)
    again = batch.promote(f, PLAN, ["u.example.org"], write=True)
    assert again["u.example.org"].promoted == {} and again["u.example.org"].held == {}


def test_update_round2_runs_on_the_list_in_the_config_dir(tmp_path, monkeypatch, ledger, capsys):
    f, ids = ledger
    root = Path(f.path).parent
    f.close()
    conf = tmp_path / "conf"
    conf.mkdir()
    (conf / "archive_round1.json").write_text(json.dumps({"sites": SITES}), encoding="utf-8")
    monkeypatch.setenv("NKNU_CONFIG_DIR", str(conf))
    assert update.main(["--dir", str(root), "round2", "--sites", "u.example.org", "--dry-run"]) == 0
    assert "u.example.org" in capsys.readouterr().out
    assert update.main(["--dir", str(root), "round2", "--sites", "u.example.org"]) == 0
    g = Frontier(root / "frontier.db")
    try:
        assert g.row_by_id(ids["u1"])["state"] == "pending"
    finally:
        g.close()
    assert update.main(["--dir", str(root), "round2", "--sites", "u.example.org", "--undo"]) == 0
    g = Frontier(root / "frontier.db")
    try:
        assert g.row_by_id(ids["u1"])["state"] == "later"
    finally:
        g.close()


def test_a_run_holds_an_open_batch_site_by_round_one_caps_and_a_unit_by_its_own(tmp_path):
    """crawler.Crawler._cap: a site under periodic update is held by the nightly growth caps, but by a round-one site's
    caps while round two's batch is open for it; a unit of the batch by its own caps."""
    from tests.archive.test_pilot_fixes import make

    crawler, _net, clock = make(tmp_path, {})
    crawler.plan = PLAN
    f = crawler.frontier
    f.meta("update:sites", json.dumps(["ks.nknu.edu.tw"]))
    crawler.start_run_caps()
    for n in range(300):
        _row(f, f"https://ks.nknu.edu.tw/p?id={n}", "ks.nknu.edu.tw", state="pending", added_at=clock() + 1)
    assert crawler._cap("https://ks.nknu.edu.tw/q", "ks.nknu.edu.tw", "page", 2) == "cap:run_page"
    batch.promote(f, PLAN, ["ks.nknu.edu.tw"], write=True)
    crawler.start_run_caps()
    assert crawler._cap("https://ks.nknu.edu.tw/q", "ks.nknu.edu.tw", "page", 2) is None
    for n in range(2):
        _row(f, f"https://u.example.org/p{n}", "u.example.org", state="pending")
    assert crawler._cap("https://u.example.org/q", "u.example.org", "page", 2) == "cap:site"
    assert crawler._cap("https://u.example.org/f.pdf", "u.example.org", "file", 2) is None


def test_a_site_with_only_round_two_rows_is_not_ready_to_export(ledger):
    """design 2026-11-17-round2-batch2 §三.7: nothing of it is queued, yet nothing of it was fetched either."""
    from nknu.archive import export

    f, _ids = ledger
    with f.lock:
        got = export.readiness(f.conn, 0.0, ["u.example.org", "ks.nknu.edu.tw"])
    assert not got["u.example.org"].can and "下一轮" in got["u.example.org"].state_words
    assert got["ks.nknu.edu.tw"].can  # its about page was fetched, nothing of it is queued


def test_the_nightly_export_lists_update_sites_only(tmp_path, capsys):
    """design 2026-11-17-round2-batch2 §三.5: a round-two batch's site a day's run fetched is in the night's change list
    (watch.night reads every row fetched since the last review) but is exported when its batch is done, not by the
    night: the export list keeps the sites under periodic update."""
    from tests.archive.test_nightly_export_1116 import _status

    g = Frontier(tmp_path / "frontier.db")
    g.meta("update:sites", json.dumps(["a.nknu.edu.tw", "b.nknu.edu.tw"]))
    g.close()
    _status(tmp_path, sites_changed=["a.nknu.edu.tw", "u.example.org"])
    assert update.main(["--dir", str(tmp_path), "export-list"]) == 0
    assert capsys.readouterr().out.split() == ["a.nknu.edu.tw"]
