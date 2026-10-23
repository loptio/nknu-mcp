"""`nknu review` for items still waiting in the queue: the crawler writes directly since U2, so these are left over from
an older version (or added by hand); the command must keep working and never put older content back."""

import sqlite3
from dataclasses import replace
from datetime import date

import pytest
import typer
from typer.testing import CliRunner

from nknu.admin.cli import app
from nknu.admin.review import NotPending, decide, list_pending, render_item
from nknu.build.templates import load_templates
from nknu.core.factsdb import connect, data_version
from nknu.core.records import HoursRule, content_hash
from nknu.core.resolve import SQLiteBackend, resolve
from nknu.core.writer import add_pending, current_fact
from nknu.crawl.pipeline import run_adapter
from nknu.settings import REPO_ROOT
from tests.crawl.test_pipeline import FakeAdapter
from tests.factories import T0, TERM_BEGIN, WR_TERM, make_db

TPL = load_templates(REPO_ROOT / "config" / "templates.yaml")
TODAY = date(2026, 10, 22)


def queue(conn, *recs, adapter="pe_open_hours"):
    for rec in recs:
        add_pending(conn, adapter=adapter, record_key=rec.key, op="upsert", record=rec, content_hash=content_hash(rec),
                    diff=f"+ {rec.key}", reason="new", now=T0)
    conn.commit()


@pytest.fixture
def conn(tmp_path):
    c = make_db(tmp_path)
    queue(c, WR_TERM)
    yield c
    c.close()


def test_render_shows_diff_quote_and_preview(conn):
    s = render_item(conn, list_pending(conn)[0], TODAY, TPL)
    assert "19:00" in s and "Page.aspx?PN=8" in s and "現在" in s and "核准後" in s and "週一、二、三、五 19:00–21:00" in s


def test_preview_does_not_write(conn):
    n = data_version(conm := conn)
    facts_before = conm.execute("SELECT count(*) FROM fact").fetchone()[0]
    render_item(conn, list_pending(conn)[0], TODAY, TPL)
    assert data_version(conn) == n and conn.execute("SELECT count(*) FROM fact").fetchone()[0] == facts_before


def test_approve_writes_verified(conn):
    decide(conn, list_pending(conn)[0].id, "approve", now=T0)
    assert current_fact(conn, WR_TERM.key)["review"] == "verified" and not list_pending(conn)
    assert conn.execute("SELECT content_hash FROM record_state WHERE record_key=?", (WR_TERM.key,)).fetchone()


def test_reject_writes_nothing(conn):
    decide(conn, list_pending(conn)[0].id, "reject", now=T0)
    assert current_fact(conn, WR_TERM.key) is None
    assert conn.execute("SELECT status FROM pending_change").fetchone()[0] == "rejected"


def test_approve_event_and_retire(tmp_path):
    from dataclasses import replace

    c = make_db(tmp_path, calendar=False)
    queue(c, replace(TERM_BEGIN, status="tentative"), adapter="aca_calendar_ics")
    decide(c, list_pending(c)[0].id, "approve", now=T0)
    assert c.execute("SELECT status FROM calendar_event").fetchone()[0] == "tentative"


def _env(tmp_path):
    return {"NKNU_DATA_DIR": str(tmp_path)}


def test_cli_approve_all(conn, tmp_path):
    conn.commit()
    r = CliRunner().invoke(app, ["review", "--no-build"], input="y\n", env=_env(tmp_path))
    assert r.exit_code == 0, r.output
    assert not list_pending(connect(tmp_path / "facts.db"))


def test_cli_one_by_one(tmp_path):
    c = make_db(tmp_path)
    from dataclasses import replace

    queue(c, WR_TERM, replace(WR_TERM, key="k2", entity_id="nknu.yanchao.gym.weight-room"),
          replace(WR_TERM, key="k3", entity_id="nknu.heping.gym.main"))
    c.close()
    r = CliRunner().invoke(app, ["review", "--no-build"], input="e\na\nr\ns\n", env=_env(tmp_path))
    assert r.exit_code == 0, r.output
    c = connect(tmp_path / "facts.db")
    assert sorted(x[0] for x in c.execute("SELECT status FROM pending_change")) == ["approved", "pending", "rejected"]


def test_readonly_refuses_review(conn, tmp_path):
    r = CliRunner().invoke(app, ["review"], input="y\n", env={**_env(tmp_path), "NKNU_READONLY": "1"})
    assert r.exit_code != 0


def test_add_manual_extension(tmp_path):
    make_db(tmp_path).close()
    r = CliRunner().invoke(app, ["add", "hours", "--place", "nknu.heping.library.main", "--from", "2026-10-24", "--opens", "08:15",
                                 "--closes", "21:00", "--reason", "期中考周延長開館", "--source-url",
                                 "https://lis.nknu.edu.tw/news/2380", "--publisher", "nknu.org.lis", "--no-build"],
                           env=_env(tmp_path))
    assert r.exit_code == 0, r.output
    c = connect(tmp_path / "facts.db")
    res = resolve(SQLiteBackend(c), "nknu.heping.library.main", "hours", date(2026, 10, 24))
    assert res["today"]["segments"] == [{"opens": "08:15", "closes": "21:00"}] and res["winner"]["tier"] == 2


def test_add_closed_range(tmp_path):
    make_db(tmp_path).close()
    r = CliRunner().invoke(app, ["add", "hours", "--place", "和平重訓室", "--from", "2026-11-02", "--to", "2026-11-03",
                                 "--closed", "--reason", "器材維修", "--source-url", "https://c.nknu.edu.tw/x",
                                 "--publisher", "nknu.org.pe", "--site-visit", "--no-build"], env=_env(tmp_path))
    assert r.exit_code == 0, r.output
    c = connect(tmp_path / "facts.db")
    res = resolve(SQLiteBackend(c), "nknu.heping.gym.weight-room", "hours", date(2026, 11, 3))
    assert res["today"]["open"] is False and res["winner"]["tier"] == 4


def test_add_ambiguous_place_errors(tmp_path):
    make_db(tmp_path).close()
    r = CliRunner().invoke(app, ["add", "hours", "--place", "圖書館", "--from", "2026-10-24", "--closed", "--reason", "x",
                                 "--source-url", "https://x", "--publisher", "nknu.org.lis", "--no-build"], env=_env(tmp_path))
    assert r.exit_code != 0 and "nknu.heping.library.main" in r.output


def _break(conn, item_id):
    # the record names a place that is not in the vocabulary: writing it fails on the entity foreign key
    conn.execute("UPDATE pending_change SET record_json=json_set(record_json, '$.entity_id', 'nknu.heping.gym.gone') WHERE id=?",
                 (item_id,))
    conn.commit()


def test_failed_approval_writes_nothing(conn):
    item = list_pending(conn)[0]
    _break(conn, item.id)
    n, sources = data_version(conn), conn.execute("SELECT count(*) FROM source_doc").fetchone()[0]
    with pytest.raises(sqlite3.IntegrityError):
        decide(conn, item.id, "approve", now=T0)
    assert not conn.in_transaction and data_version(conn) == n
    assert conn.execute("SELECT count(*) FROM source_doc").fetchone()[0] == sources and list_pending(conn)


def test_cli_review_survives_a_broken_item(tmp_path):
    # F-review-crash: one item that cannot be previewed or applied is reported and skipped; the others are approved
    c = make_db(tmp_path)
    queue(c, WR_TERM, replace(WR_TERM, key="k2", entity_id="nknu.yanchao.gym.weight-room"))
    bad, good = list_pending(c)
    _break(c, bad.id)
    c.close()
    r = CliRunner().invoke(app, ["review", "--no-build"], input="y\n", env=_env(tmp_path))
    assert r.exit_code == 1 and not isinstance(r.exception, sqlite3.Error), r.output
    assert f"#{bad.id}" in r.output and "失败" in r.output and "已处理 1 条" in r.output
    c = connect(tmp_path / "facts.db")
    assert dict(c.execute("SELECT id, status FROM pending_change").fetchall()) == {bad.id: "pending", good.id: "approved"}


def test_cli_review_skips_an_item_a_crawl_replaced(conn, tmp_path, monkeypatch):
    # F-review-race: the 2-hourly crawl writes that key itself while the maintainer is reading the list; approving the
    # older item afterwards would put older content back
    conn.commit()
    newer = replace(WR_TERM, rules=(HoursRule((1, 2, 3, 4, 5), "18:00", "21:00"),))
    prompt = typer.prompt

    def crawl_then_prompt(text, **kw):
        if "全部核准" in text:
            other = connect(tmp_path / "facts.db")
            run_adapter(other, FakeAdapter([newer], name="pe_open_hours"), None, today=TODAY, full_scan=False, readonly=False)
            other.close()
        return prompt(text, **kw)

    monkeypatch.setattr(typer, "prompt", crawl_then_prompt)
    r = CliRunner().invoke(app, ["review", "--no-build"], input="y\n", env=_env(tmp_path))
    assert r.exit_code == 0, r.output
    assert "取代" in r.output and "已处理 0 条" in r.output
    c = connect(tmp_path / "facts.db")
    assert not list_pending(c)
    assert c.execute("SELECT opens FROM hours_rule WHERE fact_id=?", (current_fact(c, WR_TERM.key)["id"],)).fetchone()[0] \
        == "18:00"


def test_decide_on_a_superseded_item(conn):
    item = list_pending(conn)[0]
    run_adapter(conn, FakeAdapter([replace(WR_TERM, rules=(HoursRule((1,), "18:00", "21:00"),))], name="pe_open_hours"), None,
                today=TODAY, full_scan=False, readonly=False)
    with pytest.raises(NotPending):
        decide(conn, item.id, "approve", now=T0)
