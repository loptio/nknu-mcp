"""`nknu changes` and `nknu undo` (C3): the maintainer's after-the-fact control over automatically written data."""

from dataclasses import replace

import pytest
from typer.testing import CliRunner

from nknu.admin.cli import app
from nknu.admin.review import list_pending
from nknu.core.factsdb import connect
from nknu.core.writer import current_fact
from nknu.crawl.adapters.aca_calendar_ics import ICS_URL, AcaCalendarIcs
from nknu.crawl.adapters.lib_open_time import URL as LIB_URL
from nknu.crawl.adapters.lib_open_time import LibOpenTime
from tests.crawl.test_identity import ICS, LIB_PAGE, RETRO_UID, approve_all, crawl, edited
from tests.crawl.test_pipeline import FakeAdapter
from tests.factories import DOC, make_db

CLOSED = "lib_open_time:nknu.heping.library.main:closed:2026-10-25"


def cli(tmp_path, *args, env=None):
    return CliRunner().invoke(app, list(args), env={"NKNU_DATA_DIR": str(tmp_path), **(env or {})})


@pytest.fixture
def lib(tmp_path):
    conn = make_db(tmp_path)
    crawl(conn, LibOpenTime(), LIB_PAGE, LIB_URL)
    approve_all(conn)
    conn.close()
    return tmp_path


def test_changes_lists_writes_with_their_ids(lib):
    fid = current_fact(connect(lib / "facts.db"), CLOSED)["id"]
    r = cli(lib, "changes", "--since", "2026-10-01")
    assert r.exit_code == 0, r.output
    assert "新增 [lib_open_time] 和平圖書館 2026-10-25～2026-10-26 不開放" in r.output
    assert f"id {fid}  key {CLOSED}" in r.output


def test_changes_show_replaced_and_undone(lib):
    conn = connect(lib / "facts.db")
    crawl(conn, LibOpenTime(), edited(LIB_PAGE, "(10/25-10/26)", "(10/24-10/26)"), LIB_URL)
    approve_all(conn)
    conn.close()
    assert cli(lib, "undo", "lib_open_time:nknu.yanchao.library.main:closed:2026-10-24", "--no-build").exit_code == 0
    out = cli(lib, "changes", "--since", "2026-10-01").output
    assert "被取代 [lib_open_time] 和平圖書館 2026-10-25～2026-10-26 不開放（臺灣光復暨金門古寧頭大捷紀念日及補假）" \
           "（由 lib_open_time:nknu.heping.library.main:closed:2026-10-24 取代）" in out
    assert "撤回 [lib_open_time] 燕巢圖書館 2026-10-24～2026-10-26" in out


def test_undo_by_fact_id_and_refuse_an_old_version(lib):
    conn = connect(lib / "facts.db")
    fid = current_fact(conn, CLOSED)["id"]
    conn.close()
    r = cli(lib, "undo", fid, "--no-build")
    assert r.exit_code == 0 and CLOSED in r.output, r.output
    r = cli(lib, "undo", fid, "--no-build")
    assert r.exit_code == 2 and "不是现行版本" in r.output
    assert cli(lib, "undo", "no-such-thing", "--no-build").exit_code == 2


def test_undo_rebuilds_serve_db(lib):
    r = cli(lib, "undo", CLOSED)
    assert r.exit_code == 0 and "serve.db built" in r.output, r.output
    assert (lib / "serve.db").exists()


def test_undo_cancel_lets_the_crawler_write_it_again(lib):
    assert cli(lib, "undo", CLOSED, "--no-build").exit_code == 0
    r = cli(lib, "undo", CLOSED, "--cancel")
    assert r.exit_code == 0 and "已忘记 1 份" in r.output, r.output
    conn = connect(lib / "facts.db")
    crawl(conn, LibOpenTime(), LIB_PAGE, LIB_URL)
    assert CLOSED in {i.record_key for i in list_pending(conn)}


def test_undo_calendar_event(tmp_path):
    conn = make_db(tmp_path, calendar=False)
    crawl(conn, AcaCalendarIcs(), ICS, ICS_URL)
    conn.close()
    assert cli(tmp_path, "undo", RETRO_UID, "--no-build").exit_code == 0
    conn = connect(tmp_path / "facts.db")
    status = "SELECT status FROM calendar_event WHERE uid=?"
    assert conn.execute(status, (RETRO_UID,)).fetchone()[0] == "withdrawn"
    crawl(conn, AcaCalendarIcs(), ICS, ICS_URL)
    assert conn.execute(status, (RETRO_UID,)).fetchone()[0] == "withdrawn"
    crawl(conn, AcaCalendarIcs(), edited(ICS, "SUMMARY:放假日(臺灣光復節)", "SUMMARY:放假日(臺灣光復節連假)"), ICS_URL)
    assert conn.execute(status, (RETRO_UID,)).fetchone()[0] == "confirmed"  # changed content is written again


def test_undo_announcement(tmp_path):
    conn = make_db(tmp_path)
    crawl_doc = FakeAdapter([DOC], name="sso_news")
    from nknu.crawl.pipeline import run_adapter

    run_adapter(conn, crawl_doc, None, today=None, full_scan=False, readonly=False)
    conn.close()
    assert cli(tmp_path, "undo", DOC.key, "--no-build").exit_code == 0
    conn = connect(tmp_path / "facts.db")
    run_adapter(conn, crawl_doc, None, today=None, full_scan=False, readonly=False)
    assert conn.execute("SELECT withdrawn_at FROM doc WHERE doc_key=?", (DOC.doc_key,)).fetchone()[0] is not None
    run_adapter(conn, FakeAdapter([replace(DOC, title="115學年度第1學期停修申請（更正）")], name="sso_news"), None, today=None,
                full_scan=False, readonly=False)
    assert conn.execute("SELECT withdrawn_at FROM doc WHERE doc_key=?", (DOC.doc_key,)).fetchone()[0] is None


def test_undo_refused_when_readonly(lib):
    r = cli(lib, "undo", CLOSED, "--no-build", env={"NKNU_READONLY": "1"})
    assert r.exit_code == 1 and current_fact(connect(lib / "facts.db"), CLOSED)
