import json
from datetime import date

import pytest

from nknu.server import envelope
from nknu.server.envelope import (
    ECHO_BYTES,
    ERROR_BUDGET,
    NOTICE_LABEL,
    count_tokens,
    echo,
    error_result,
    make_envelope,
    render_text,
    to_tool_result,
)
from nknu.server.query import get_calendar, get_hours
from nknu.server.servedb_reader import ServeDB
from tests.server.conftest import NOW

TODAY = {"date": "2026-10-22", "weekday": "週四", "time": "14:05"}


def leaf_values(obj):
    """Every str/int leaf of an envelope (bools and None skipped)."""
    if isinstance(obj, dict):
        for v in obj.values():
            yield from leaf_values(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from leaf_values(v)
    elif obj is not None and not isinstance(obj, bool):
        yield obj


def test_make_envelope_omits_none_and_empty():
    env = make_envelope(kb="k", today=TODAY, found=False, status="no_data", match=None, answer="", facts=[],
                        recent_notices=None, caveat="查無資料，請勿推測", similar=[])
    assert env == {"kb": "k", "today": TODAY, "found": False, "status": "no_data", "caveat": "查無資料，請勿推測"}


def test_render_text_order_and_labels():
    env = make_envelope(kb="k1", today=TODAY, found=True, status="current", match="entity", answer="甲館開放。來源：乙。",
                        facts=[{"id": "f1", "valid": ["2026-09-07", "2027-01-08"],
                                "rules": [{"dows": [1, 2], "opens": "08:00", "closes": "12:00"}]}],
                        caveat="注意事項", open_now=False)
    lines = render_text(env).splitlines()
    assert lines[0] == "甲館開放。來源：乙。"
    assert "f1" in lines[1] and "2026-09-07～2027-01-08" in lines[1] and "週1,2 08:00–12:00" in lines[1]
    assert any("current" in ln for ln in lines[2:]) and any(ln.endswith("否") for ln in lines)


def test_text_equivalent_to_structured(sample_envelopes):
    for name, env in sample_envelopes.items():
        t = render_text(env)
        missing = [v for v in leaf_values(env) if str(v) not in t]
        assert not missing, (name, missing)


def test_samples_cover_the_statuses(sample_envelopes):
    s = sample_envelopes
    assert [s[k]["status"] for k in ("current", "conflict", "stale", "no_data")] == ["current", "conflict", "stale", "no_data"]
    assert len(s["conflict"]["alternatives"]) == 4 and len(s["days14"]["days"]) == 14 and s["notices"]["recent_notices"]
    for name, env in s.items():
        assert env["status"] and (env["status"] == "current" or env.get("caveat")), name


def test_no_answer_budget_is_left():
    """U1: the 400-token budget and its trim ladder are gone; only the error length is still bounded."""
    assert not hasattr(envelope, "fit_budget") and not hasattr(envelope, "BUDGET")


def test_make_envelope_keeps_long_content():
    notices = [{"title": "公" * 80, "published_on": "2026-10-20", "url": "https://example.test/" + "x" * 60,
                "label": NOTICE_LABEL} for _ in range(5)]
    days = [{"date": f"2026-10-{d}", "weekday": "週四", "status": "current", "open": True, "segments": ["19:00–21:00"]}
            for d in range(10, 24)]
    fact = {"id": "f", "valid": ["2026-09-07", "2027-01-08"], "source": {"title": "表", "url": "u", "tier": 1},
            "rules": [{"dows": [1], "opens": "08:00", "closes": "12:00"}]}
    fields = {"found": True, "status": "current", "match": "entity", "answer": "甲" * 600 + "附註：" + "乙" * 600,
              "facts": [fact], "next_change": {"from": "2026-10-24", "answer": "丙" * 300}, "recent_notices": notices,
              "caveat": "請勿推測"}
    env = make_envelope(kb="k", today=TODAY, days=days, **fields)
    assert env == {"kb": "k", "today": TODAY, **fields, "days": days}


def _card(db: ServeDB, place: str, d: date) -> str | None:
    row = db.conn().execute("SELECT answer_zh FROM answer_card WHERE entity_id=? AND intent='hours' AND valid_from<=? "
                            "AND ?<valid_to", (place, d.isoformat(), d.isoformat())).fetchone()
    return row[0] if row else None


def test_every_hours_envelope_is_complete(world):
    """Every place x 3 dates x days 1/7/14: the card sentence whole (附註 and 來源 included), one full entry per day, the
    fact's rules/validity/source, and next_change as a sentence."""
    db = ServeDB(world[1])
    places = [r[0] for r in db.conn().execute("SELECT DISTINCT entity_id FROM answer_card WHERE intent='hours'")]
    for p in places:
        for d in ("2026-10-22", "2026-12-24", "2027-02-01"):
            for n in (1, 7, 14):
                e = get_hours(db, p, d, days=n, now=NOW)
                assert e["answer"] == _card(db, p, date.fromisoformat(d)), (p, d)
                assert "…" not in json.dumps(e, ensure_ascii=False) and "more_days" not in e
                if n > 1:
                    assert len(e["days"]) == n and all(x["weekday"] and x["status"] for x in e["days"]), (p, d, n)
                for f in e.get("facts") or []:
                    assert {"id", "valid", "rules", "source"} <= set(f) and {"url", "tier"} <= set(f["source"]), (p, d)
                if e.get("next_change"):
                    assert e["next_change"]["answer"], (p, d)


def test_every_calendar_event_in_the_limit_is_listed(world):
    e = get_calendar(ServeDB(world[1]), "2026-09-01", "2027-01-31", now=NOW, limit=50)
    assert len(e["facts"]) == min(50, int(e["answer"].split("共 ")[1].split(" 筆")[0]))
    assert e.get("more", 0) + len(e["facts"]) == int(e["answer"].split("共 ")[1].split(" 筆")[0])


def test_error_result_is_short_error():
    r = error_result("bad_days", "days 應為 1–14")
    assert r.is_error and r.structured_content == {"code": "bad_days", "message": "days 應為 1–14"}
    assert "bad_days" in r.content[0].text and count_tokens(r.content[0].text) <= ERROR_BUDGET


def test_echo_quotes_user_input_cut_by_bytes():
    assert echo("2026-13-45") == "「2026-13-45」"
    assert echo("a\nb\x00") == "「a?b?」"  # no line breaks or control characters inside an error
    cut = echo("㐀" * 100)
    assert cut.endswith("…」") and len(cut[1:-2].encode()) <= ECHO_BYTES
    assert count_tokens(echo("𠀀" * 100)) <= ECHO_BYTES + 4  # a token is at least one byte


def test_error_result_with_long_cjk_input_stays_short():
    for msg in ["日期格式應為 YYYY-MM-DD 或民國 115-10-24：" + echo("二〇二六年十月二十二日" * 40),
                "日期格式應為 YYYY-MM-DD 或民國 115-10-24：" + echo("㐀㐁㐂㐃" * 100),
                "參數不合法：" + echo("𠀀😀" * 50) + " 不支援此參數", "說明文字" * 300, "x" * 2000]:
        r = error_result("bad_argument", msg)
        assert count_tokens(r.content[0].text) <= ERROR_BUDGET and count_tokens(r.structured_content) <= ERROR_BUDGET, msg
        assert r.content[0].text == f"錯誤 bad_argument：{r.structured_content['message']}"
    assert error_result("bad_argument", "x" * 2000).structured_content["message"].endswith("…")


def test_to_tool_result_carries_both_forms(sample_envelopes):
    env = sample_envelopes["current"]
    r = to_tool_result(env)
    assert r.structured_content == env and r.content[0].text == render_text(env) and not r.is_error


@pytest.mark.parametrize("obj", ["和平校區", {"a": [1, 2]}, [{"x": "y"}]])
def test_count_tokens_accepts_any_json(obj):
    assert count_tokens(obj) > 0
