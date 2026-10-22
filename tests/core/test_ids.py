from datetime import UTC, date, datetime
from uuid import UUID

from nknu.core.ids import ENTITY_ID_RE, lineage_id, uuid7
from nknu.core.timeutil import iso_now, today_tpe


def test_today_tpe_crosses_utc_midnight():
    assert today_tpe(datetime(2026, 10, 22, 16, 30, tzinfo=UTC)) == date(2026, 10, 23)
    assert today_tpe(datetime(2026, 10, 22, 15, 59, tzinfo=UTC)) == date(2026, 10, 22)


def test_iso_now_format():
    s = iso_now()
    assert s.endswith("Z") and len(s) == 20


def test_uuid7_sorted_and_version():
    a, b = uuid7(), uuid7()
    assert UUID(a).version == 7 and UUID(a).variant == "specified in RFC 4122" and a < b


def test_lineage_id_stable():
    assert lineage_id("pe_open_hours:x:hours") == lineage_id("pe_open_hours:x:hours") != lineage_id("other")


def test_entity_id_rule():
    for ok in ("nknu", "nknu.heping", "nknu.org.pe", "nknu.heping.gym.weight-room", "nknu.yanchao.library.main"):
        assert ENTITY_ID_RE.match(ok), ok
    for bad in ("nknu.place.weight-room", "nknu.taipei.gym.x", "nknu.heping.gym", "NKNU.heping.gym.x"):
        assert not ENTITY_ID_RE.match(bad), bad
