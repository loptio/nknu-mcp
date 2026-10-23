"""Fact identity across crawls (C1), the undo memory (C3) and the disappearance rules (C2): pipeline replays over edited
copies of the real fixtures."""

from dataclasses import replace
from datetime import UTC, date, datetime

from typer.testing import CliRunner

from nknu.admin.cli import app
from nknu.admin.review import list_pending
from nknu.core.factsdb import connect
from nknu.core.records import FactRecord, content_hash
from nknu.core.resolve import SQLiteBackend, resolve
from nknu.core.vocab import fill_validity
from nknu.core.writer import apply_fact, current_fact, set_state
from nknu.crawl.adapters.aca_calendar_ics import ICS_URL, AcaCalendarIcs
from nknu.crawl.adapters.lib_open_time import URL as LIB_URL
from nknu.crawl.adapters.lib_open_time import LibOpenTime
from nknu.crawl.adapters.pe_news import DETAIL_META as PE_DETAIL_META
from nknu.crawl.adapters.pe_news import LIST_META as PE_LIST_META
from nknu.crawl.adapters.pe_news import LIST_URL as PE_LIST_URL
from nknu.crawl.adapters.pe_news import PeNews
from nknu.crawl.adapters.pe_news import detail_url as pe_detail_url
from nknu.crawl.adapters.pe_open_hours import URL as PE_URL
from nknu.crawl.adapters.pe_open_hours import PeOpenHours
from nknu.crawl.pipeline import run_adapter
from tests.crawl.adapters.helpers import FIXTURES, read, snap
from tests.factories import make_db
from tests.world import Replay

TODAY = date(2026, 10, 22)
NOW = "2026-10-22T03:00:00Z"
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


def cli(tmp_path, *args, input=None):
    return CliRunner().invoke(app, list(args), input=input, env={"NKNU_DATA_DIR": str(tmp_path)})


def test_moved_library_closure_replaces_the_old_one(tmp_path):
    # F-review-crash: the site moves a closure's first day (10/25-10/26 -> 10/24-10/26); the record key carries the
    # first day, so this is a new lineage on the same page that must replace the old one, not trip the overlap trigger
    conn = make_db(tmp_path)
    crawl(conn, LibOpenTime(), LIB_PAGE, LIB_URL)
    moved = edited(LIB_PAGE, "(10/25-10/26)", "(10/24-10/26)")
    res = crawl(conn, LibOpenTime(), moved, LIB_URL, full_scan=True)
    assert res.n_written == len(LIBS) and not list_pending(conn)  # U2: written at once, no review queue
    be = SQLiteBackend(conn)
    for eid in LIBS:
        assert current_fact(conn, f"lib_open_time:{eid}:closed:2026-10-25") is None
        assert current_fact(conn, f"lib_open_time:{eid}:closed:2026-10-24") is not None
        assert resolve(be, eid, "hours", date(2026, 10, 24))["today"]["open"] is False
    res = crawl(conn, LibOpenTime(), moved, LIB_URL, full_scan=True)  # the next crawl: nothing to do, no crash
    assert (res.n_written, res.n_retired) == (0, 0)


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
    next_term = edited(edited(PE_PAGE, "115.09.07", "116.02.22"), "116.01.08", "116.06.30")
    for _ in range(4):  # C2: this term's table left the page, but it is dated and expires by itself
        crawl(conn, PeOpenHours(), next_term, PE_URL, full_scan=True)
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
    current = conn.execute("SELECT entity_id, record_key FROM fact WHERE recorded_to IS NULL AND specificity=1").fetchall()
    assert len(current) == 11 and len({r[0] for r in current}) == 11
    assert all(r[1].endswith(":2026-09-07") for r in current)
    assert not any(current_fact(conn, k) for k in old_keys)
    retired = conn.execute(f"SELECT count(*) FROM record_state WHERE status='retired' AND record_key IN "
                           f"({','.join('?' * len(old_keys))})", sorted(old_keys)).fetchone()[0]
    assert retired == 11
    for real, page, url in ((PeOpenHours(), PE_PAGE, PE_URL), (LibOpenTime(), LIB_PAGE, LIB_URL)):
        res = crawl(conn, real, page, url, full_scan=True)
        assert (res.n_written, res.n_retired) == (0, 0), real.name


def test_undone_fact_is_not_written_back(tmp_path):
    # C3: `nknu undo` takes the current version down and the crawler never writes identical content back
    conn = make_db(tmp_path)
    crawl(conn, LibOpenTime(), LIB_PAGE, LIB_URL)
    conn.close()
    key = "lib_open_time:nknu.heping.library.main:closed:2026-10-25"
    r = cli(tmp_path, "undo", key, "--no-build")
    assert r.exit_code == 0, r.output
    conn = connect(tmp_path / "facts.db")
    assert current_fact(conn, key) is None
    res = crawl(conn, LibOpenTime(), LIB_PAGE, LIB_URL, full_scan=True)
    assert (res.n_written, res.n_retired) == (0, 0) and current_fact(conn, key) is None
    # changed content (one more closure day) is a new statement and is written again
    longer = edited(LIB_PAGE, "(10/25-10/26)", "(10/25-10/27)")
    crawl(conn, LibOpenTime(), longer, LIB_URL)
    assert current_fact(conn, key)["valid_to"] == "2026-10-28"


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
    conn.close()
    r = cli(tmp_path, "add", "hours", "--place", "nknu.heping.library.main", "--from", "2026-10-24", "--to", "2026-10-26",
            "--closed", "--reason", "臺灣光復節連假", "--source-url", LIB_URL, "--publisher", "nknu.org.lis", "--no-build")
    assert r.exit_code == 0, r.output
    conn = connect(tmp_path / "facts.db")
    crawled = "lib_open_time:nknu.heping.library.main:closed:2026-10-25"
    assert current_fact(conn, crawled) is None
    res = crawl(conn, LibOpenTime(), LIB_PAGE, LIB_URL, full_scan=True)
    assert res.n_written == 0 and current_fact(conn, crawled) is None
    assert resolve(SQLiteBackend(conn), "nknu.heping.library.main", "hours", date(2026, 10, 24))["winner"]["tier"] == 2


PE_LIST = read("pe_news", "list.html")
PE_CLOSURE = read("pe_news", "closure.html")
GYM_1024 = "pe_news:460:nknu.heping.gym.main:2026-10-24"
GYM_1225 = "pe_news:460:nknu.heping.gym.main:2026-12-25"


def pe_crawl(conn, list_html: str, detail_html: str | None = None, *, status: int = 200, full_scan: bool = True):
    snaps = [snap(PE_LIST_URL, list_html, meta=PE_LIST_META)]
    if detail_html is not None or status != 200:
        snaps.append(snap(pe_detail_url("460"), detail_html, status=status, meta=PE_DETAIL_META))
    res = run_adapter(conn, Replay(PeNews(), snaps), None, today=TODAY, full_scan=full_scan, readonly=False)
    assert res.passed, (res.violations, res.error)
    return res


def from_460(conn) -> int:
    return conn.execute("SELECT count(*) FROM fact WHERE record_key LIKE 'pe_news:460:%' AND recorded_to IS NULL").fetchone()[0]


def test_closure_stated_only_in_the_body_is_not_retired(tmp_path):
    # F-pe-closure-false-retire: a title without 暫停開放 means full scans do not read the detail page again; its
    # closures were not "missing" from those runs, and they are written without review (U2)
    conn = make_db(tmp_path)
    plain = edited(PE_LIST, "於115年10月24至26日，全日暫停開放", "國定假日及連續假日場館公告")
    pe_crawl(conn, plain, PE_CLOSURE)
    assert current_fact(conn, GYM_1024)["review"] == "auto_ok" and not list_pending(conn)
    for _ in range(4):
        assert pe_crawl(conn, plain).n_retired == 0
    assert current_fact(conn, GYM_1024) and from_460(conn) == 28 and not list_pending(conn)
    assert conn.execute("SELECT max(missing_full_scans) FROM record_state WHERE adapter='pe_news'").fetchone()[0] == 0


def test_closure_removed_from_a_reread_announcement_is_retired(tmp_path):
    # C2: the announcement's detail page was read on three full scans and no longer states the 10/24 closure
    conn = make_db(tmp_path)
    pe_crawl(conn, PE_LIST, PE_CLOSURE)
    without = edited(PE_CLOSURE, "115年10月24日（週六）至10月26日（週一） 臺灣光復紀念日連假", "")
    results = [pe_crawl(conn, PE_LIST, without) for _ in range(3)]
    assert [r.n_retired for r in results] == [0, 0, 2] and not list_pending(conn)
    assert current_fact(conn, GYM_1024) is None and current_fact(conn, GYM_1225) and from_460(conn) == 26


def test_closure_announcement_answering_404_retires_its_closures(tmp_path):
    # C2: a dated closure is taken down when its page is gone (404), together with the announcement
    conn = make_db(tmp_path)
    pe_crawl(conn, PE_LIST, PE_CLOSURE)
    res = pe_crawl(conn, PE_LIST, status=404)
    assert from_460(conn) == 0 and res.n_retired == 1 + 28
    assert conn.execute("SELECT withdrawn_at FROM doc WHERE doc_key='460'").fetchone()[0] is not None
    assert pe_crawl(conn, PE_LIST, status=404).n_written == 0  # F-404-churn: nothing left to write


def test_events_leaving_the_ics_window_are_not_retired(tmp_path):
    # C2: as the parse window moves on, past events stop being produced; they were not deleted from the calendar
    conn = make_db(tmp_path, calendar=False)
    crawl(conn, AcaCalendarIcs(), ICS, ICS_URL)
    later = date(2027, 1, 15)  # 註冊及全校上課開始 (2026-09-07) is now before the window
    for _ in range(4):
        s = replace(snap(ICS_URL, ICS, body=ICS.encode()), fetched_at=datetime(2027, 1, 15, 2, 0, tzinfo=UTC))
        res = run_adapter(conn, Replay(AcaCalendarIcs(), [s]), None, today=later, full_scan=True, readonly=False)
        assert res.passed and res.n_retired == 0, (res.violations, res.error)
    assert conn.execute("SELECT status FROM calendar_event WHERE uid='285io5346ii0sed306em64cj2k@google.com'").fetchone()[0] \
        == "confirmed"
