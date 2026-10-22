"""A resolve() scenario world built through the real writer. Values mirror the Oct 2026 NKNU pages."""

from dataclasses import replace
from datetime import date

from nknu.core.records import FactRecord, HoursRule, SourceRef
from nknu.core.writer import apply_fact
from tests.factories import HOLIDAY, LIB_REGULAR, LIB_TABLE, PE_TABLE, T0, WR_TERM, make_db

WR, YWR, GYM, OUTDOOR = ("nknu.heping.gym.weight-room", "nknu.yanchao.gym.weight-room", "nknu.heping.gym.main",
                         "nknu.heping.gym.outdoor")
HLIB, YLIB, POOL = "nknu.heping.library.main", "nknu.yanchao.library.main", "nknu.heping.gym.pool"
COURT, NIGHT = "nknu.heping.gym.outdoor-court", "nknu.heping.gym.night-test"  # test-only entities

PE_800 = SourceRef("https://c.nknu.edu.tw/reality/NewsView.aspx?Nid=800", "高師大體育館概況及開放時間", "nknu.org.pe",
                   "announcement", "sha-800", published_on=date(2022, 9, 16))
PE_460 = SourceRef("https://c.nknu.edu.tw/reality/NewsView.aspx?Nid=460",
                   "本校體育館(含室外運動場地)於115年10月24至26日，全日暫停開放", "nknu.org.pe", "announcement", "sha-460",
                   published_on=date(2025, 12, 26))
LIB_2380 = SourceRef("https://lis.nknu.edu.tw/news/2380", "115學年度第一學期，兩校區圖書館期中考延長開館時間表", "nknu.org.lis",
                     "announcement", "sha-2380", published_on=date(2026, 10, 15))


def fact(key, entity, src, valid, rules=(), spec=1, closed=False, reason=None, propagates=False, basis="explicit",
         extraction="rule"):
    value = {"closed": closed} | ({"reason": reason} if reason else {})
    return FactRecord(key=key, entity_id=entity, attribute="hours", value=value, valid_from=valid[0], valid_to=valid[1],
                      valid_basis=basis, specificity=spec, propagates=propagates, source=src, quote=key, extraction=extraction,
                      rules=tuple(rules))


def build(tmp_path):
    conn = make_db(tmp_path)
    conn.execute("INSERT INTO entity(id, type, name_zh, campus) VALUES (?, 'Place', '測試球場', 'heping')", (COURT,))
    conn.execute("INSERT INTO entity(id, type, name_zh, campus) VALUES (?, 'Place', '測試夜間場館', 'heping')", (NIGHT,))
    conn.execute("INSERT INTO entity_relation(subject, predicate, object) VALUES (?, 'containedInPlace', ?)", (COURT, OUTDOOR))
    conn.execute("INSERT INTO entity_relation(subject, predicate, object) VALUES (?, 'managedBy', 'nknu.org.pe')", (COURT,))
    closure = (date(2026, 10, 24), date(2026, 10, 27))
    term_lib = (date(2026, 9, 7), date(2027, 1, 18))
    d24, d25 = (date(2026, 10, 24), date(2026, 10, 25)), (date(2026, 10, 25), date(2026, 10, 26))
    recs = [
        ("pe_open_hours", WR_TERM),
        ("pe_open_hours", replace(WR_TERM, key="pe:ywr", entity_id=YWR,
                                  rules=(HoursRule((1, 2, 3, 4, 5), "18:00", "21:00"),))),
        ("pe_open_hours", fact("pe:court", COURT, PE_TABLE, (date(2026, 9, 7), date(2027, 1, 9)),
                               [HoursRule((1, 2, 3, 4, 5, 6, 7), "06:00", "22:00")])),
        ("pe_open_hours", fact("pe:night", NIGHT, PE_TABLE, (date(2026, 9, 7), date(2027, 1, 9)),
                               [HoursRule((5,), "22:00", "02:00")])),
        ("pe_news", fact("pe800:wr", WR, PE_800, (date(2022, 9, 16), date(2023, 1, 16)),
                         [HoursRule((1, 5), "19:00", "21:50"), HoursRule((2, 3, 4), "18:30", "21:30")], spec=0, basis="ttl")),
        ("pe_news", fact("pe460:gym", GYM, PE_460, closure, spec=2, closed=True, reason="臺灣光復紀念日連假", propagates=True)),
        ("pe_news", fact("pe460:outdoor", OUTDOOR, PE_460, closure, spec=2, closed=True, reason="臺灣光復紀念日連假")),
        ("lib_open_time", replace(LIB_REGULAR, valid_from=term_lib[0], valid_to=term_lib[1])),
        ("lib_open_time", replace(LIB_REGULAR, key="lib:ylib", entity_id=YLIB, valid_from=term_lib[0], valid_to=term_lib[1],
                                  rules=(HoursRule((1, 2, 3, 4, 5), "08:15", "21:30"), HoursRule((6, 7), "09:15", "16:30")))),
        ("lib_open_time", fact("lib:closed:h", HLIB, LIB_TABLE, d25, spec=2, closed=True, reason="臺灣光復暨金門古寧頭大捷紀念日")),
        ("lib_open_time", fact("lib:closed:y", YLIB, LIB_TABLE, d25, spec=2, closed=True, reason="臺灣光復暨金門古寧頭大捷紀念日")),
        ("manual", fact("manual:h:1024", HLIB, LIB_2380, d24, [HoursRule((6,), "08:15", "21:00")], spec=2,
                        reason="期中考周延長開館", extraction="manual")),
        ("manual", fact("manual:y:1024", YLIB, LIB_2380, d24, [HoursRule((6,), "09:15", "21:00")], spec=2,
                        reason="期中考周延長開館", extraction="manual")),
        ("manual", fact("manual:h:1025", HLIB, LIB_2380, d25, spec=2, closed=True, reason="閉館", extraction="manual")),
        ("aca_calendar_ics", HOLIDAY),
    ]
    for adapter, rec in recs:
        apply_fact(conn, rec, adapter=adapter, review="verified" if rec.attribute == "hours" else "auto_ok", now=T0)
    conn.commit()
    return conn
