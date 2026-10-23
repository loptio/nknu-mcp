"""Fact identity across crawls (C1) and the undo memory (C3): pipeline replays over edited copies of the real fixtures."""

from dataclasses import replace
from datetime import date

from typer.testing import CliRunner

from nknu.admin.cli import app
from nknu.admin.review import decide, list_pending, render_item
from nknu.build.templates import load_templates
from nknu.core.factsdb import connect
from nknu.core.records import FactRecord, content_hash
from nknu.core.resolve import SQLiteBackend, resolve
from nknu.core.vocab import fill_validity
from nknu.core.writer import apply_fact, current_fact, set_state
from nknu.crawl.adapters.aca_calendar_ics import ICS_URL, AcaCalendarIcs
from nknu.crawl.adapters.lib_open_time import URL as LIB_URL
from nknu.crawl.adapters.lib_open_time import LibOpenTime
from nknu.crawl.adapters.pe_open_hours import URL as PE_URL
from nknu.crawl.adapters.pe_open_hours import PeOpenHours
from nknu.crawl.pipeline import run_adapter
from nknu.settings import REPO_ROOT
from tests.crawl.adapters.helpers import FIXTURES, read, snap
from tests.factories import make_db
from tests.world import Replay

TODAY = date(2026, 10, 22)
NOW = "2026-10-22T03:00:00Z"
TPL = load_templates(REPO_ROOT / "config" / "templates.yaml")
LIBS = ("nknu.heping.library.main", "nknu.yanchao.library.main", "nknu.heping.library.iread", "nknu.yanchao.library.iread")
WR = "nknu.heping.gym.weight-room"
LIB_PAGE = read("lib_open_time", "page.html")
PE_PAGE = read("pe_open_hours", "page.html")
ICS = (FIXTURES / "aca_calendar_ics" / "calendar.ics").read_bytes().decode()
RETRO_UID = "45ddkd2cuoblc1f9b9jvo212g7@google.com"  # 放假日(臺灣光復節) 10/24–10/26
DOUBLE_TENTH_UID = "416toqdmtcs9su9fbh23emqmi6@google.com"  # 放假日(雙十連假) 10/09–10/11


def edited(text: str, old: str, new: str) -> str:
    assert old in text, old
    return text.replace(old, new)


def crawl(conn, real, text: str, url: str, *, full_scan: bool = False):
    res = run_adapter(conn, Replay(real, [snap(url, text, body=text.encode())]), None, today=TODAY, full_scan=full_scan,
                      readonly=False)
    assert res.passed, (res.violations, res.error)
    return res


def approve_all(conn) -> None:
    for item in list_pending(conn):
        decide(conn, item.id, "approve", NOW)


def cli(tmp_path, *args, input=None):
    return CliRunner().invoke(app, list(args), input=input, env={"NKNU_DATA_DIR": str(tmp_path)})


def test_moved_library_closure_replaces_the_old_one(tmp_path):
    # F-review-crash: the site moves a closure's first day (10/25-10/26 -> 10/24-10/26); the record key carries the
    # first day, so this is a new lineage on the same page that must replace the old one, not trip the overlap trigger
    conn = make_db(tmp_path)
    crawl(conn, LibOpenTime(), LIB_PAGE, LIB_URL)
    approve_all(conn)
    moved = edited(LIB_PAGE, "(10/25-10/26)", "(10/24-10/26)")
    crawl(conn, LibOpenTime(), moved, LIB_URL, full_scan=True)
    items = list_pending(conn)
    assert sorted(i.record_key for i in items) == sorted(f"lib_open_time:{e}:closed:2026-10-24" for e in LIBS)
    preview = render_item(conn, items[0], TODAY, TPL)
    assert "連帶下架" in preview and ":closed:2026-10-25" in preview  # the maintainer sees what approval replaces
    conn.close()

    r = cli(tmp_path, "review", "--no-build", input="y\n")
    assert r.exit_code == 0, r.output
    conn = connect(tmp_path / "facts.db")
    be = SQLiteBackend(conn)
    for eid in LIBS:
        assert current_fact(conn, f"lib_open_time:{eid}:closed:2026-10-25") is None
        assert current_fact(conn, f"lib_open_time:{eid}:closed:2026-10-24") is not None
        assert resolve(be, eid, "hours", date(2026, 10, 24))["today"]["open"] is False
    res = crawl(conn, LibOpenTime(), moved, LIB_URL, full_scan=True)  # the next crawl: nothing to do, no crash
    assert (res.n_written, res.n_pending) == (0, 0)


def test_holiday_recreated_under_a_new_uid(tmp_path):
    # F-ics-holiday-uid: a holiday deleted and re-created in Google Calendar gets a new UID (= a new record key)
    conn = make_db(tmp_path, calendar=False)
    crawl(conn, AcaCalendarIcs(), ICS, ICS_URL)
    assert current_fact(conn, f"ics-holiday:{RETRO_UID}")
    new_uid = "0recreated0holiday0000000@google.com"
    recreated = edited(ICS, f"UID:{RETRO_UID}", f"UID:{new_uid}")
    crawl(conn, AcaCalendarIcs(), recreated, ICS_URL)
    assert current_fact(conn, f"ics-holiday:{RETRO_UID}") is None and current_fact(conn, f"ics-holiday:{new_uid}")
    assert conn.execute("SELECT 1 FROM calendar_event WHERE uid=?", (new_uid,)).fetchone()
    # the calendar keeps updating afterwards
    renamed = edited(recreated, "SUMMARY:期末考試Final exams", "SUMMARY:期末考試Final exams week")
    crawl(conn, AcaCalendarIcs(), renamed, ICS_URL)
    assert conn.execute("SELECT 1 FROM calendar_event WHERE summary='期末考試Final exams week'").fetchone()


EXTRA_HOLIDAY = ("BEGIN:VEVENT\r\nSUMMARY:國慶日放假\r\nDTSTART;VALUE=DATE:20261010\r\nDTEND;VALUE=DATE:20261011\r\n"
                 "UID:0extra0holiday00000000000@google.com\r\nSEQUENCE:0\r\nSTATUS:CONFIRMED\r\nEND:VEVENT\r\n")


def test_overlapping_holidays_in_one_calendar(tmp_path):
    # F-ics-holiday-uid: a second holiday overlapping 雙十連假 in the same calendar neither crashes the run nor makes the
    # two replace each other on every crawl; the earlier-starting one is kept and the maintainer is told
    conn = make_db(tmp_path, calendar=False)
    both = edited(ICS, "BEGIN:VEVENT\r\nSUMMARY:放假日(雙十連假)", EXTRA_HOLIDAY + "BEGIN:VEVENT\r\nSUMMARY:放假日(雙十連假)")
    crawl(conn, AcaCalendarIcs(), both, ICS_URL)
    assert current_fact(conn, f"ics-holiday:{DOUBLE_TENTH_UID}")
    assert current_fact(conn, "ics-holiday:0extra0holiday00000000000@google.com") is None
    assert conn.execute("SELECT count(*) FROM alert WHERE code='overlap'").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM calendar_event WHERE category='holiday'").fetchone()[0] == 3  # events untouched
    for _ in range(2):
        assert crawl(conn, AcaCalendarIcs(), both, ICS_URL).n_written == 0


def test_next_term_table_does_not_close_this_term(tmp_path):
    # F-next-term-early: 體育室 posts next term's table before this term ends
    conn = make_db(tmp_path)
    crawl(conn, PeOpenHours(), PE_PAGE, PE_URL)
    approve_all(conn)
    next_term = edited(edited(PE_PAGE, "115.09.07", "116.02.22"), "116.01.08", "116.06.30")
    crawl(conn, PeOpenHours(), next_term, PE_URL)
    approve_all(conn)
    assert current_fact(conn, f"pe_open_hours:{WR}:hours:2026-09-07")
    assert current_fact(conn, f"pe_open_hours:{WR}:hours:2027-02-22")
    be = SQLiteBackend(conn)
    assert resolve(be, WR, "hours", date(2026, 10, 23))["winner"]["valid"][0] == "2026-09-07"
    assert resolve(be, WR, "hours", date(2027, 3, 1))["winner"]["valid"][0] == "2027-02-22"


def test_old_keys_are_replaced_by_term_keys(tmp_path):
    # existing facts.db files carry term tables under the old keys (pe_open_hours:<eid>:hours, lib_open_time:<eid>:regular);
    # the first crawl with term keys replaces those lineages: no crash, never two current term tables for one place
    conn = make_db(tmp_path)
    old_keys = set()
    for real, page, url in ((PeOpenHours(), PE_PAGE, PE_URL), (LibOpenTime(), LIB_PAGE, LIB_URL)):
        for rec in real.parse(snap(url, page)):
            if isinstance(rec, FactRecord) and rec.specificity == 1:
                key = rec.key.removesuffix(f":{rec.valid_from}") if rec.valid_from else rec.key
                filled = replace(fill_validity(conn, rec, TODAY), key=key)
                apply_fact(conn, filled, adapter=real.name, review="verified", now=NOW)
                set_state(conn, real.name, key, "fact", content_hash(filled), NOW)
                old_keys.add(key)
    conn.commit()
    assert len(old_keys) == 11 and all(k.endswith((":hours", ":regular")) for k in old_keys)

    for real, page, url in ((PeOpenHours(), PE_PAGE, PE_URL), (LibOpenTime(), LIB_PAGE, LIB_URL)):
        crawl(conn, real, page, url, full_scan=True)
    approve_all(conn)
    current = conn.execute("SELECT entity_id, record_key FROM fact WHERE recorded_to IS NULL AND specificity=1").fetchall()
    assert len(current) == 11 and len({r[0] for r in current}) == 11
    assert all(r[1].endswith(":2026-09-07") for r in current)
    assert not any(current_fact(conn, k) for k in old_keys)
    retired = conn.execute(f"SELECT count(*) FROM record_state WHERE status='retired' AND record_key IN "
                           f"({','.join('?' * len(old_keys))})", sorted(old_keys)).fetchone()[0]
    assert retired == 11
    for real, page, url in ((PeOpenHours(), PE_PAGE, PE_URL), (LibOpenTime(), LIB_PAGE, LIB_URL)):
        res = crawl(conn, real, page, url, full_scan=True)
        assert (res.n_written, res.n_pending) == (0, 0), real.name


def test_undone_fact_is_not_written_back(tmp_path):
    # C3: `nknu undo` takes the current version down and the crawler never writes identical content back
    conn = make_db(tmp_path)
    crawl(conn, LibOpenTime(), LIB_PAGE, LIB_URL)
    approve_all(conn)
    conn.close()
    key = "lib_open_time:nknu.heping.library.main:closed:2026-10-25"
    r = cli(tmp_path, "undo", key, "--no-build")
    assert r.exit_code == 0, r.output
    conn = connect(tmp_path / "facts.db")
    assert current_fact(conn, key) is None
    res = crawl(conn, LibOpenTime(), LIB_PAGE, LIB_URL, full_scan=True)
    assert (res.n_written, res.n_pending) == (0, 0) and current_fact(conn, key) is None
    # changed content (one more closure day) is a new statement and is proposed again
    longer = edited(LIB_PAGE, "(10/25-10/26)", "(10/25-10/27)")
    crawl(conn, LibOpenTime(), longer, LIB_URL)
    assert key in {i.record_key for i in list_pending(conn)}


def test_manual_entry_corrected_with_the_same_url(tmp_path):
    # F-manual-no-undo: a mistyped range is corrected by entering it again with the same announcement URL
    make_db(tmp_path).close()
    base = ["add", "hours", "--place", "nknu.heping.library.main", "--closed", "--reason", "停電", "--source-url",
            "https://lis.nknu.edu.tw/news/9999", "--publisher", "nknu.org.lis", "--no-build"]
    assert cli(tmp_path, *base, "--from", "2026-11-02", "--to", "2026-11-04").exit_code == 0
    r = cli(tmp_path, *base, "--from", "2026-11-03", "--to", "2026-11-04")
    assert r.exit_code == 0, r.output
    conn = connect(tmp_path / "facts.db")
    assert current_fact(conn, "manual:nknu.heping.library.main:2026-11-02:2026-11-04") is None
    assert current_fact(conn, "manual:nknu.heping.library.main:2026-11-03:2026-11-04")
    be = SQLiteBackend(conn)
    assert resolve(be, "nknu.heping.library.main", "hours", date(2026, 11, 2))["status"] == "no_data"
    assert resolve(be, "nknu.heping.library.main", "hours", date(2026, 11, 3))["today"]["open"] is False
    conn.close()
    # a typo that does not overlap the right range is taken down with undo
    assert cli(tmp_path, *base, "--from", "2027-11-03").exit_code == 0
    r = cli(tmp_path, "undo", "manual:nknu.heping.library.main:2027-11-03:2027-11-03", "--no-build")
    assert r.exit_code == 0, r.output
    assert current_fact(connect(tmp_path / "facts.db"), "manual:nknu.heping.library.main:2027-11-03:2027-11-03") is None


def test_manual_entry_over_crawled_content_sticks(tmp_path):
    # a maintainer's entry that replaces a crawled fact of the same page is not overwritten by the next crawl
    conn = make_db(tmp_path)
    crawl(conn, LibOpenTime(), LIB_PAGE, LIB_URL)
    approve_all(conn)
    conn.close()
    r = cli(tmp_path, "add", "hours", "--place", "nknu.heping.library.main", "--from", "2026-10-24", "--to", "2026-10-26",
            "--closed", "--reason", "臺灣光復節連假", "--source-url", LIB_URL, "--publisher", "nknu.org.lis", "--no-build")
    assert r.exit_code == 0, r.output
    conn = connect(tmp_path / "facts.db")
    crawled = "lib_open_time:nknu.heping.library.main:closed:2026-10-25"
    assert current_fact(conn, crawled) is None
    res = crawl(conn, LibOpenTime(), LIB_PAGE, LIB_URL, full_scan=True)
    assert res.n_pending == 0 and current_fact(conn, crawled) is None
    assert resolve(SQLiteBackend(conn), "nknu.heping.library.main", "hours", date(2026, 10, 24))["winner"]["tier"] == 2
