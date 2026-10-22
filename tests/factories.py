"""Record factories with values taken from the real NKNU pages (Oct 2026)."""

from datetime import date

from nknu.core.factsdb import connect, migrate
from nknu.core.records import DocRecord, EventRecord, FactRecord, HoursRule, SourceRef
from nknu.core.vocab import load_vocab, seed
from nknu.settings import REPO_ROOT

T0 = "2026-10-22T02:00:00Z"
T1 = "2026-11-01T02:00:00Z"
PE_URL = "https://c.nknu.edu.tw/reality/Page.aspx?PN=8"
LIB_URL = "https://lis.nknu.edu.tw/open-time"
ICS_URL = "https://calendar.google.com/calendar/ical/test/public/basic.ics"

PE_TABLE = SourceRef(PE_URL, "場地開放時間", "nknu.org.pe", "table_page", "sha-pe-1")
LIB_TABLE = SourceRef(LIB_URL, "詳細開館時間", "nknu.org.lis", "table_page", "sha-lib-1")
ICS = SourceRef(ICS_URL, "教學行事曆", "nknu.org.aca", "ics", "sha-ics-1")

WR_TERM = FactRecord(
    key="pe_open_hours:nknu.heping.gym.weight-room:hours", entity_id="nknu.heping.gym.weight-room", attribute="hours",
    value={"closed": False}, valid_from=date(2026, 9, 7), valid_to=date(2027, 1, 9), valid_basis="explicit", specificity=1,
    propagates=False, source=PE_TABLE, quote="重量訓練室 115.09.07至116.01.08 週一、二、三、五 19：00~21：00 週四 18：00~20：00",
    extraction="rule", rules=(HoursRule((1, 2, 3, 5), "19:00", "21:00"), HoursRule((4,), "18:00", "20:00")),
)
LIB_REGULAR = FactRecord(
    key="lib_open_time:nknu.heping.library.main:regular", entity_id="nknu.heping.library.main", attribute="hours",
    value={"closed": False}, valid_from=None, valid_to=None, valid_basis="term", specificity=1, propagates=False,
    source=LIB_TABLE, quote="開館時間 Open Hours of Regular Semester 08:15~22:00 08:15~17:00", extraction="rule",
    rules=(HoursRule((1, 2, 3, 4, 5), "08:15", "22:00"), HoursRule((6, 7), "08:15", "17:00")),
)
HOLIDAY = FactRecord(
    key="ics-holiday:hol-1024", entity_id="nknu", attribute="holiday", value={"label": "臺灣光復暨金門古寧頭大捷紀念日放假"},
    valid_from=date(2026, 10, 24), valid_to=date(2026, 10, 27), valid_basis="explicit", specificity=2, propagates=False,
    source=ICS, quote="臺灣光復暨金門古寧頭大捷紀念日放假", extraction="ics",
)
TERM_BEGIN = EventRecord("ev-term", "ev-term", "115學年度第1學期開始上課", date(2026, 9, 7), date(2026, 9, 8), "term_begin",
                         "confirmed", 0, None, ICS)
VACATION = EventRecord("ev-vac", "ev-vac", "寒假開始", date(2027, 1, 18), date(2027, 1, 19), "vacation_begin", "confirmed", 0,
                       None, ICS)
DOC = DocRecord(key="sso_news:A1", doc_key="A1", title="115學年度第1學期停修申請", url="https://sso.nknu.edu.tw/News/lastNews.aspx?UNID=A1",
                unit="教務處", category="SCHOOLADMIN", published_on=date(2026, 10, 20), announce_until=date(2026, 11, 16))


def make_db(tmp_path, *, calendar=True):
    conn = connect(tmp_path / "facts.db")
    migrate(conn)
    seed(conn, load_vocab(REPO_ROOT / "config" / "entities.yaml"))
    if calendar:
        from nknu.core.writer import upsert_event

        for ev in (TERM_BEGIN, VACATION):
            upsert_event(conn, ev, adapter="aca_calendar_ics", now=T0)
    conn.commit()
    return conn
