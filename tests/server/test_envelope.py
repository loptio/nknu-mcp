import copy
from datetime import date, timedelta

import pytest

from nknu.server.envelope import (
    BUDGET,
    ECHO_BYTES,
    ERROR_BUDGET,
    NOTICE_LABEL,
    approx_tokens,
    count_tokens,
    echo,
    error_result,
    fit_budget,
    make_envelope,
    render_text,
    to_tool_result,
)
from nknu.server.query import get_calendar, get_hours
from nknu.server.servedb_reader import ServeDB
from tests.server.conftest import NOW, covered_days

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
    assert len(s["conflict"]["alternatives"]) == 3 and covered_days(s["days14"]["days"]) == 14 and s["notices"]["recent_notices"]


def test_envelope_token_budget(sample_envelopes):
    for name, env in sample_envelopes.items():  # current, conflict (3 alternatives), days=14, stale, no_data, notices
        assert count_tokens(env) <= BUDGET, (name, count_tokens(env))
        assert env["status"] and (env["status"] == "current" or env.get("caveat")), name
    assert count_tokens(error_result("bad_date", "date 格式應為 YYYY-MM-DD").structured_content or {}) <= ERROR_BUDGET


def test_budget_holds_for_every_place_and_span(world):
    db = ServeDB(world[1])
    places = [r[0] for r in db.conn().execute("SELECT DISTINCT entity_id FROM answer_card WHERE intent='hours'")]
    envs = [get_hours(db, p, d, days=n, now=NOW) for p in places for d in ("2026-10-22", "2026-12-24", "2027-02-01")
            for n in (1, 7, 14)]
    envs += [get_hours(db, q, "2026-10-22", now=NOW) for q in ("重訓室", "圖書館", "愛閱館", "室外運動場地")]
    envs += [get_calendar(db, "2026-09-01", "2027-01-31", now=NOW, limit=50)]
    for env in envs:
        real = count_tokens(env)
        assert real <= BUDGET, (env.get("answer"), real)
        assert approx_tokens(env) >= real  # the serving-side estimate never undercounts


def test_fit_budget_trims_in_order_and_keeps_status_and_caveat():
    notices = [{"title": "公" * 80, "published_on": "2026-10-20", "url": "https://example.test/" + "x" * 60,
                "label": NOTICE_LABEL} for _ in range(3)]
    days = [{"date": (date(2026, 10, 22) + timedelta(days=i)).isoformat(), "weekday": "週四", "status": "current", "open": True,
             "segments": ["19:00–21:00"]} for i in range(7)]
    env = make_envelope(kb="k", today=TODAY, found=True, status="current", match="entity", answer="甲" * 60 + "附註：" + "乙" * 60,
                        facts=[{"id": "f", "valid": ["2026-09-07", "2027-01-08"], "source": {"title": "表", "url": "u"}}],
                        recent_notices=notices, caveat="請勿推測", days=days)
    out = fit_budget(env, 300)
    assert approx_tokens(out) <= 300 and out["status"] == "current" and out["caveat"] == "請勿推測"
    assert covered_days(out["days"]) == 7 and out["days"][0]["segments"] == ["19:00–21:00"]
    assert out["answer"].startswith("甲" * 60)
    assert env["recent_notices"][0]["title"] == "公" * 80  # the input is not mutated


def test_fit_budget_truncates_notice_titles_first():
    notices = [{"title": "公" * 80, "published_on": "2026-10-20", "url": "u", "label": NOTICE_LABEL}]
    env = make_envelope(kb="k", today=TODAY, found=True, status="current", answer="甲", recent_notices=notices,
                        days=[{"date": "2026-10-22", "weekday": "週四", "status": "current", "open": True}])
    out = fit_budget(env, approx_tokens(env) - 10)
    assert len(out["recent_notices"][0]["title"]) <= 41 and out["days"][0]["weekday"] == "週四"


def test_fit_budget_drops_next_answer_before_the_answer_notes():
    env = make_envelope(kb="k", today=TODAY, found=True, status="current", answer="甲" * 40 + "附註：" + "乙" * 40,
                        next_change={"from": "2026-10-24", "answer": "丙" * 80 + "附註：" + "丁" * 40})
    out = fit_budget(env, approx_tokens(env) - 60)
    assert out["answer"] == env["answer"] and out["next_change"] == {"from": "2026-10-24"}


def test_fit_budget_slims_candidates_before_cutting_one():
    """An ambiguous list loses its candidates' 附註, then their 來源 clauses, then their next_change sentences."""
    def cand(i: int) -> dict:
        return {"entity_id": f"e{i}", "name": "館", "status": "current", "answer": "甲" * 50 + "。來源：處《表》。附註：" + "乙" * 60,
                "next_change": {"from": "2026-10-24", "answer": "丙" * 30 + "。來源：處《" + "丁" * 30 + "》。"}}

    env = make_envelope(kb="k", today=TODAY, found=True, status="current", match="ambiguous", facts=[cand(1), cand(2)],
                        caveat="請確認校區")
    no_notes = copy.deepcopy(env)
    for f in no_notes["facts"]:
        f["answer"] = f["answer"].split("附註：")[0]
    out = fit_budget(env, approx_tokens(no_notes))
    assert [f["answer"] for f in out["facts"]] == ["甲" * 50 + "。來源：處《表》。"] * 2
    assert all(f["next_change"]["answer"].endswith("》。") for f in out["facts"])
    no_sources = copy.deepcopy(no_notes)
    for f in no_sources["facts"]:
        f["answer"], f["next_change"]["answer"] = "甲" * 50 + "。", "丙" * 30 + "。"
    out = fit_budget(env, approx_tokens(no_sources))
    assert len(out["facts"]) == 2 and [f["next_change"]["answer"] for f in out["facts"]] == ["丙" * 30 + "。"] * 2
    out = fit_budget(env, approx_tokens(no_sources) - 10)
    assert len(out["facts"]) == 2 and [f["next_change"] for f in out["facts"]] == [{"from": "2026-10-24"}] * 2


def test_fit_budget_cuts_days_then_alternatives_last():
    alts = [{"id": f"a{i}", "rules": [{"dows": [6], "opens": f"0{i + 7}:00", "closes": "12:00"}],
             "source": {"url": f"https://c.nknu.edu.tw/reality/NewsView.aspx?Nid=900{i}"}} for i in range(3)]
    days = [{"date": "2026-11-14", "weekday": "週六", "status": "conflict"}] + [
        {"date": (date(2026, 11, 15) + timedelta(days=i)).isoformat(), "weekday": "週日", "status": "current", "open": True,
         "segments": [f"{18 + i % 2}:00–21:00"]} for i in range(13)]  # alternating hours: no runs to merge
    env = make_envelope(kb="k", today=TODAY, found=True, status="conflict", answer="甲" * 150, alternatives=alts,
                        caveat="請勿推測", days=days)
    out = fit_budget(env, 300)
    assert approx_tokens(out) <= 300 and out["answer"] == env["answer"] and out["caveat"] == "請勿推測"
    assert out["days"] == [{"date": "2026-11-14", "status": "conflict"}] and out["more_days"] == 13
    assert 1 <= len(out["alternatives"]) < 3 and out["more"] == 3 - len(out["alternatives"])


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
