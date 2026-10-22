from datetime import date

import pytest
from typer.testing import CliRunner

from nknu.admin.cli import app
from nknu.admin.review import decide, list_pending, render_item
from nknu.build.templates import load_templates
from nknu.core.factsdb import connect, data_version
from nknu.core.resolve import SQLiteBackend, resolve
from nknu.core.writer import current_fact
from nknu.crawl.pipeline import run_adapter
from nknu.settings import REPO_ROOT
from tests.crawl.test_pipeline import FakeAdapter
from tests.factories import T0, TERM_BEGIN, WR_TERM, make_db

TPL = load_templates(REPO_ROOT / "config" / "templates.yaml")
TODAY = date(2026, 10, 22)


@pytest.fixture
def conn(tmp_path):
    c = make_db(tmp_path)
    run_adapter(c, FakeAdapter([WR_TERM], name="pe_open_hours"), None, today=TODAY, full_scan=True, readonly=False)
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
    run_adapter(c, FakeAdapter([replace(TERM_BEGIN, status="tentative")], name="aca_calendar_ics"), None, today=TODAY,
                full_scan=True, readonly=False)
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

    recs = [WR_TERM, replace(WR_TERM, key="k2", entity_id="nknu.yanchao.gym.weight-room"),
            replace(WR_TERM, key="k3", entity_id="nknu.heping.gym.main")]
    run_adapter(c, FakeAdapter(recs, name="pe_open_hours"), None, today=TODAY, full_scan=True, readonly=False)
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
