"""11-17 (the owner, relayed by 爬虫judge: 「全部不抓」 — the one page off campus a school page links, round2:offsite and
round2:offsite_file, never fetched; this changes the 11-16 direction paper's 「链出去的校外页只抓那一页」): such a row
keeps its place (later, its reason) and reads as the rule, never as round two owed: the views' result 'rule', the
export's words saying the owner decided so. The 30 off-campus units' own pages (round2:offsite_unit) and other round-two
rows are still later. Made-up rows."""

from __future__ import annotations

import sqlite3

import pytest

from nknu.archive import export, monitor, outcome, progress
from nknu.archive.frontier import Frontier


@pytest.fixture
def f(tmp_path):
    fr = Frontier(tmp_path / "frontier.db")
    yield fr
    fr.close()


def _later(f, url: str, reason: str, kind: str = "page") -> int:
    f.add(url, host=url.split("/")[2], site=url.split("/")[2], unit=None, tier=2, kind=kind, depth=1, found_via="link",
          found_from=None, state="later", reason=reason, owner_site=url.split("/")[2], leaf=1, kind0=kind)
    return f.row(url)["id"]


def _result(f, uid: int) -> str:
    outcome.create_views(f.conn, temp=True)
    return f.q("SELECT result FROM url_result WHERE id=?", (uid,))[0][0]


@pytest.mark.parametrize("reason,kind", [("round2:offsite", "page"), ("round2:offsite_file", "file")])
def test_a_page_off_campus_a_school_page_links_reads_as_the_rule(f, reason, kind):
    uid = _later(f, "https://news.example.org/story/1" + (".pdf" if kind == "file" else ""), reason, kind)
    assert _result(f, uid) == "rule"
    assert "用户" in export._why("rule", reason) and "不抓" in export._why("rule", reason)


@pytest.mark.parametrize("reason", ["round2:offsite_unit", "round2:js_site", "round2:other_site"])
def test_other_round_two_rows_stay_later(f, reason):
    assert _result(f, _later(f, "https://unit.example.org/", reason)) == "later"


def test_the_dashboard_has_words_for_it():
    assert "用户" in progress.ROUND2_ZH["round2:offsite"] and "用户" in progress.ROUND2_ZH["round2:offsite_file"]
    assert sqlite3  # the views are sqlite


def test_a_school_page_sent_off_campus_reads_as_the_rule_with_its_own_words():
    """爬虫judge 11-17: a round-one page that sends the browser to such a page (32 on the lab, mostly notices the
    school moved to another university's site) is the rule's too, not 要人看, with words saying why."""
    code = "rule_offsite"
    assert monitor.bucket(code, page=True) == "rule" and monitor.tone(code, page=True) == monitor.GOOD
    assert outcome.RESULT_LABEL[code].startswith("按规则不抓")
    assert export._why(code, None) == "整页跳到校外页（用户 11-17 决定不抓）"
    assert code in progress.PAGE_ORDER and progress.LABEL_COLOR[code] == "none"
    assert "用户 11-17 决定不抓" in dict(outcome.LABEL_ORDER)[code] and outcome.LEVEL_LABEL["rule"] == "按规则不抓"
