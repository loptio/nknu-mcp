import json
import sqlite3
from dataclasses import replace
from datetime import date

import pytest

from nknu.build.servedb import build_serve_db, rollback
from nknu.build.templates import dows_zh, span_zh
from nknu.core.factsdb import connect, data_version
from nknu.core.records import HoursRule, SourceRef
from nknu.core.textnorm import fts_query, tokenize
from nknu.core.writer import apply_fact
from nknu.settings import REPO_ROOT
from tests.factories import PE_URL, T0, WR_TERM
from tests.scenario import WR, build

TPL = REPO_ROOT / "config" / "templates.yaml"
TODAY = date(2026, 10, 22)


@pytest.fixture
def world(tmp_path):
    build(tmp_path).close()
    return tmp_path


def make(world, today=TODAY, disabled=frozenset()):
    return build_serve_db(world / "facts.db", world / "serve.db", today=today, templates_path=TPL, disabled_sources=disabled)


def ro(world):
    c = sqlite3.connect(world / "serve.db")
    c.row_factory = sqlite3.Row
    return c


def card_on(world, entity, d, intent="hours"):
    return ro(world).execute("SELECT * FROM answer_card WHERE entity_id=? AND intent=? AND valid_from<=? AND ?<valid_to",
                             (entity, intent, d.isoformat(), d.isoformat())).fetchone()


def test_dows_zh():
    assert dows_zh([1, 2, 3, 5]) == "週一、二、三、五"
    assert dows_zh([1, 2, 3, 4, 5]) == "週一至週五"
    assert dows_zh([6, 7]) == "週六、日"
    assert dows_zh([1, 2, 3, 4, 5, 6, 7]) == "週一至週日"


def test_span_zh():
    assert span_zh(date(2026, 9, 7), date(2027, 1, 9)) == "2026-09-07～2027-01-08（115.09.07–116.01.08）"
    assert span_zh(date(2026, 10, 24), date(2026, 10, 25)) == "2026-10-24（115.10.24）"


def test_card_sentence_has_validity_and_source(world):
    make(world)
    a = card_on(world, WR, TODAY)["answer_zh"]
    assert "2026-09-07～2027-01-08（115.09.07–116.01.08）" in a and "週一、二、三、五 19:00–21:00" in a
    assert "來源：體育室《場地開放時間》" in a and "週六、日不開放" in a


def test_closure_segment_cut_and_merged(world):
    make(world)
    rows = ro(world).execute("SELECT valid_from, valid_to, status FROM answer_card WHERE entity_id=? AND intent='hours' "
                             "ORDER BY valid_from", (WR,)).fetchall()
    cur = [(r[0], r[1]) for r in rows if r[2] == "current"]
    assert cur[:3] == [("2026-10-15", "2026-10-24"), ("2026-10-24", "2026-10-27"), ("2026-10-27", "2027-01-09")]
    assert "暫停開放（臺灣光復紀念日連假）" in card_on(world, WR, date(2026, 10, 25))["answer_zh"]


def test_stale_card_has_no_hours(world):
    make(world, today=date(2027, 1, 25))
    c = card_on(world, WR, date(2027, 2, 1))
    assert c["status"] == "stale" and "請勿推測" in c["answer_zh"] and "體育室" in c["answer_zh"]
    assert "19:00" not in c["answer_zh"] + c["payload_json"]


def test_conflict_card_and_alert(world):
    conn = connect(world / "facts.db")
    other = SourceRef("https://c.nknu.edu.tw/reality/Page.aspx?PN=9", "場地開放時間(另一版本)", "nknu.org.pe", "table_page", "x")
    apply_fact(conn, replace(WR_TERM, key="v2", source=other, rules=(HoursRule((4,), "18:30", "20:30"),)), adapter="pe_open_hours",
               review="verified", now=T0)
    conn.commit()
    make(world)
    c = card_on(world, WR, TODAY)
    assert c["status"] == "conflict" and "場地開放時間(另一版本)" in c["answer_zh"] and "請以現場公告為準" in c["answer_zh"]
    # F-conflict-template-promise: nothing is queued for review; the maintainer is notified (C4)
    assert "已通知維護者" in c["answer_zh"] and "審核" not in c["answer_zh"] and c["template_ver"] == 2
    assert connect(world / "facts.db").execute("SELECT count(*) FROM alert WHERE code='conflict'").fetchone()[0] >= 1


def test_build_reports_new_conflicts_once(world):
    conn = connect(world / "facts.db")
    other = SourceRef("https://c.nknu.edu.tw/reality/Page.aspx?PN=9", "場地開放時間(另一版本)", "nknu.org.pe", "table_page", "x")
    apply_fact(conn, replace(WR_TERM, key="v2", source=other, rules=(HoursRule((4,), "18:30", "20:30"),)), adapter="pe_open_hours",
               review="verified", now=T0)
    conn.commit()
    assert make(world).new_conflicts >= 1
    assert make(world).new_conflicts == 0  # already alerted, not yet resolved


def test_calendar_cards(world):
    make(world)
    c = card_on(world, "nknu", date(2027, 1, 18), intent="calendar")
    assert c is not None and "寒假開始" in c["answer_zh"] and "來源：教務處《教學行事曆》" in c["answer_zh"]
    assert "暫定" not in c["answer_zh"]


def test_tentative_calendar_card_says_so(world):
    from nknu.core.writer import upsert_event
    from tests.factories import VACATION

    conn = connect(world / "facts.db")
    upsert_event(conn, replace(VACATION, status="tentative"), adapter="aca_calendar_ics", now=T0)
    conn.commit()
    make(world)
    c = card_on(world, "nknu", date(2027, 1, 18), intent="calendar")
    assert "寒假開始（暫定，日期可能變動）" in c["answer_zh"] and json.loads(c["payload_json"])["status"] == "tentative"


def test_two_char_fts_hit(world):
    make(world)
    hits = ro(world).execute("SELECT card_id FROM card_fts WHERE card_fts MATCH ?", (fts_query(tokenize("重訓")),)).fetchall()
    assert hits


def test_fact_history_copied(world):
    make(world)
    assert ro(world).execute("SELECT count(*) FROM fact").fetchone()[0] == \
        connect(world / "facts.db").execute("SELECT count(*) FROM fact").fetchone()[0]


def test_disabled_sources_excluded(world):
    make(world, disabled=frozenset({"pe_open_hours"}))
    payloads = [r[0] for r in ro(world).execute("SELECT payload_json FROM answer_card")]
    assert payloads and not any(PE_URL in p for p in payloads)
    assert ro(world).execute("SELECT count(*) FROM source_doc WHERE adapter='pe_open_hours'").fetchone()[0] == 0


def _build_id(world):
    return dict(ro(world).execute("SELECT key, value FROM meta").fetchall())["build_id"]


def test_atomic_replace_keeps_old_on_failure(world, monkeypatch):
    from nknu.build import servedb

    make(world)
    old = _build_id(world)

    def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(servedb, "_write_fts", boom)
    with pytest.raises(RuntimeError):
        make(world)
    assert _build_id(world) == old and not list(world.glob("serve.db.tmp-*"))


def test_rollback(world):
    make(world)
    a = _build_id(world)
    make(world)
    assert _build_id(world) != a
    rollback(world / "serve.db")
    assert _build_id(world) == a


def test_meta_records_facts_version(world):
    info = make(world)
    meta = dict(ro(world).execute("SELECT key, value FROM meta").fetchall())
    assert meta["facts_version"] == str(data_version(connect(world / "facts.db"))) and meta["build_id"] == info.build_id
    assert meta["today"] == "2026-10-22" and json.loads(meta["template_ver"]) == 2


def test_cli_build(world):
    from typer.testing import CliRunner

    from nknu.admin.cli import app

    r = CliRunner().invoke(app, ["build", "--today", "2026-10-22"], env={"NKNU_DATA_DIR": str(world)})
    assert r.exit_code == 0, r.output
    assert (world / "serve.db").exists()


def test_source_notes_rendered():
    from nknu.build.cards import hours_sentence
    from nknu.build.templates import load_templates

    res = {"status": "current", "winner": {
        "valid": ["2026-09-07", "2027-01-09"], "source": {"publisher": "nknu.org.pe", "title": "場地開放時間", "url": "u"},
        "value": {"closed": False, "notes": ["1.國定及連續假日、運動會停課、招生考試,各運動場地不予開放。"]},
        "rules": [{"dows": [1, 2, 3, 4, 5], "opens": "17:30", "closes": "21:50"}]}}
    s = hours_sentence(WR, res, {WR: "和平校區重量訓練室", "nknu.org.pe": "體育室"}, "體育室", load_templates(TPL),
                       date(2026, 10, 22), date(2026, 10, 23))
    assert s.endswith("附註：1.國定及連續假日、運動會停課、招生考試,各運動場地不予開放。")
