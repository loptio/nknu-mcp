"""The output envelope shared by every tool. structuredContent IS the compact payload for the model; the text block is
rendered from the same dict by render_text (Claude Code / VS Code / Codex read structuredContent, Gemini CLI reads text).

Budget (spec §8): an envelope stays within 400 o200k tokens, an error within 80. tiktoken is a dev dependency, so the
serving path uses approx_tokens(), a conservative estimate (tests check it never undercounts), and fit_budget() trims in a
fixed order. status, caveat and the core answer sentence are never removed."""

from __future__ import annotations

import copy
import datetime as dt
import json
import math
import re
from collections.abc import Callable

from fastmcp.tools import ToolResult
from mcp.types import TextContent

NOTICE_LABEL = "公告摘要，非核實事實"
BUDGET = 400
ERROR_BUDGET = 80
NOTES_MARK = "附註："
TITLE_MAX = 40


def _empty(v: object) -> bool:
    return v is None or (isinstance(v, (str, list, dict, tuple)) and not v)


def make_envelope(*, kb: str, today: dict, found: bool, status: str, match: str | None = None, answer: str | None = None,
                  facts: list | None = None, next_change: dict | None = None, last_known: dict | None = None,
                  alternatives: list | None = None, recent_notices: list | None = None, caveat: str | None = None,
                  **extra: object) -> dict:
    """Keys whose value is None or empty are left out (found=False and open_now=False are kept)."""
    env = {"kb": kb, "today": today, "found": found, "status": status, "match": match, "answer": answer, "facts": facts,
           "next_change": next_change, "last_known": last_known, "alternatives": alternatives,
           "recent_notices": recent_notices, "caveat": caveat, **extra}
    return {k: v for k, v in env.items() if not _empty(v)}


# ---------------------------------------------------------------- text rendering

_LABELS = {
    "kb": "版本", "today": "今天", "found": "找到", "status": "狀態", "match": "比對", "open_now": "現在開放",
    "next_change": "下次變動", "last_known": "最近資料", "alternatives": "不一致來源", "recent_notices": "近期公告",
    "caveat": "注意", "days": "逐日", "similar": "相近地點", "history": "版本紀錄", "more": "另有筆數", "sources": "來源",
    "id": "id", "lineage": "系列", "entity_id": "地點", "name": "名稱", "answer": "答", "valid": "有效", "rules": "時段",
    "closed": "停開", "source": "來源", "title": "標題", "url": "網址", "tier": "等級", "published_on": "發布",
    "from": "自", "valid_to_inclusive": "有效至", "source_url": "來源網址", "date": "日期", "weekday": "星期",
    "time": "時間", "open": "開放", "segments": "時段", "reason": "原因", "label": "標示", "summary": "事件",
    "category": "類別", "recorded_from": "收錄於", "recorded_to": "取代於", "superseded_by": "現行版本",
    "code": "代碼", "message": "說明", "to": "至", "more_days": "另有天數",
}


def _label(key: str) -> str:
    return _LABELS.get(key, key)


def _rule(r: dict) -> str:
    return f"週{','.join(str(d) for d in r['dows'])} {r['opens']}–{r['closes']}"


def _fmt(key: str, v: object) -> str:
    if isinstance(v, bool):
        return "是" if v else "否"
    if key == "rules" and isinstance(v, list):
        return "；".join(_rule(r) for r in v) or "無"
    if key == "valid" and isinstance(v, list) and len(v) == 2:
        return f"{v[0]}～{v[1]}"
    if key == "today" and isinstance(v, dict):
        return " ".join(str(x) for x in v.values())
    if isinstance(v, dict):
        return " ".join(f"{_label(k)}:{_fmt(k, x)}" for k, x in v.items())
    if isinstance(v, list):
        sep = "；" if any(isinstance(x, dict) for x in v) else "、"
        return sep.join(_fmt(key, x) for x in v)
    return str(v)


def render_text(env: dict) -> str:
    """One line per item: the answer first, then each fact, then every other field under a short label."""
    lines = [env["answer"]] if env.get("answer") else []
    facts = env.get("facts") or []
    lines += [f"事實{i + 1}：{_fmt('fact', f)}" if len(facts) > 1 else f"事實：{_fmt('fact', f)}" for i, f in enumerate(facts)]
    for key, v in env.items():
        if key in ("answer", "facts"):
            continue
        if isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
            lines += [f"{_label(key)}：{_fmt(key, x)}" for x in v]
        else:
            lines.append(f"{_label(key)}：{_fmt(key, v)}")
    return "\n".join(lines)


def to_tool_result(env: dict) -> ToolResult:
    return ToolResult(content=[TextContent(type="text", text=render_text(env))], structured_content=env)


def error_result(code: str, message: str) -> ToolResult:
    """A short, instruction-free error (Gemini CLI hands isError JSON to the model unwrapped)."""
    body = {"code": code, "message": message[:120]}
    return ToolResult(content=[TextContent(type="text", text=f"錯誤 {code}：{body['message']}")], structured_content=body,
                      is_error=True)


# ---------------------------------------------------------------- token budget

def _dump(obj: object) -> str:
    return obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=str)


def count_tokens(obj: object) -> int:
    """Exact o200k_base count of obj as sent (compact JSON, or the string itself). Tests only: tiktoken is a dev dependency."""
    import tiktoken

    return len(tiktoken.get_encoding("o200k_base").encode(_dump(obj)))


# Pre-tokens split the way o200k does (letter runs incl. CJK with one leading symbol, digit groups of <=3, symbol runs).
# The per-class weights were fitted (non-negative least squares) on ~13k envelopes from the fixture worlds, then scaled
# by 1.12 so the estimate stayed 1.03-1.19x the real count there; tests re-check that it never undercounts.
_HAN = "㐀-鿿豈-﫿"
_PRE = re.compile(rf"[^\r\n A-Za-z0-9{_HAN}]?[A-Za-z{_HAN}]+|\d{{1,3}}| ?[^\sA-Za-z0-9{_HAN}]+|\s+")
_HAN_CHAR = re.compile(f"[{_HAN}]")


def approx_tokens(obj: object) -> int:
    """Upper estimate of count_tokens() without a tokenizer, for the serving path."""
    n = 0.0
    for m in _PRE.finditer(_dump(obj)):
        p = m.group()
        if p.isspace():
            continue
        if p.isdigit():
            n += 1.64
            continue
        han = len(_HAN_CHAR.findall(p))
        letters = sum(c.isascii() and c.isalpha() for c in p)
        if han or letters:
            n += 0.58 + 0.99 * han + 0.143 * letters
            if letters and not any(c.islower() for c in p):
                n += 0.245  # upper-case / hex runs split into more pieces
            if not (p[0].isalnum() or _HAN_CHAR.match(p[0])):
                n += 0.83  # a leading symbol merged into the word
        else:
            wide = sum(not c.isascii() for c in p)
            n += 0.21 + 0.64 * wide + 0.22 * (len(p) - wide)
    return math.ceil(n)


def _items(env: dict, *keys: str) -> list[dict]:
    return [x for k in keys for x in env.get(k) or [] if isinstance(x, dict)]


def _short_titles(env: dict) -> None:
    """Notice titles (recent_notices, and the notices search lists as facts) are cut to TITLE_MAX characters."""
    for n in [*_items(env, "recent_notices"), *(f for f in _items(env, "facts") if f.get("label"))]:
        if len(n.get("title", "")) > TITLE_MAX:
            n["title"] = n["title"][:TITLE_MAX] + "…"


def _compact_days(env: dict) -> None:
    """Days keep date + segments (open is implied) or open=false or status; weekday goes, and so does status=current
    when a value already shows it (a stale envelope's days carry no values, so their status stays)."""
    for d in _items(env, "days"):
        d.pop("weekday", None)
        if d.get("status") == "current" and ("open" in d or d.get("segments")):
            del d["status"]
        if d.get("segments"):
            d.pop("open", None)


def _drop_source_titles(env: dict) -> None:
    for f in _items(env, "facts", "alternatives"):
        if isinstance(f.get("source"), dict):
            f["source"].pop("title", None)


def _drop_fact_rules(env: dict) -> None:
    for f in _items(env, "facts"):
        if f.get("rules"):  # an empty list costs nothing and says "no hours"
            del f["rules"]


def _drop_fact_validity(env: dict) -> None:
    """An hours fact's validity is the span its answer states and its tier is internal (calendar events keep `valid`)."""
    for f in _items(env, "facts"):
        src = f.get("source")
        if isinstance(src, dict) and "tier" in src:
            f.pop("valid", None)
            src.pop("tier", None)


def _drop_next_answer(env: dict) -> None:
    if isinstance(env.get("next_change"), dict):
        env["next_change"].pop("answer", None)


def _strip_notes_of(holders: list[dict]) -> None:
    for holder in holders:
        a = holder.get("answer")
        if isinstance(a, str) and NOTES_MARK in a:
            holder["answer"] = a.split(NOTES_MARK, 1)[0]


def _strip_fact_notes(env: dict) -> None:
    """附註 of the candidates in an ambiguous list (and of next_change, should its answer still be there)."""
    nxt = env.get("next_change")
    _strip_notes_of([*_items(env, "facts"), *([nxt] if isinstance(nxt, dict) else [])])


def _strip_answer_notes(env: dict) -> None:
    """The main answer's 附註 (e.g. 國定假日、運動會停課…不予開放) is the only signal for those days: it goes late."""
    _strip_notes_of([env])


def _drop_day_reasons(env: dict) -> None:
    for d in _items(env, "days"):
        d.pop("reason", None)


def _merge_day_runs(env: dict) -> None:
    """Consecutive days with the same (already compacted) content become one entry with an inclusive `to` date."""
    merged: list[dict] = []
    for d in _items(env, "days"):
        prev = merged[-1] if merged else None
        rest = {k: v for k, v in d.items() if k != "date"}
        if prev is not None and {k: v for k, v in prev.items() if k not in ("date", "to")} == rest:
            merged[-1] = {"date": prev["date"], "to": d["date"], **rest}
        else:
            merged.append(dict(d))
    if env.get("days"):
        env["days"] = merged


def _slim_alternatives(env: dict) -> None:
    """Conflicting sources share a tier by construction (R3 ran first); their own validity is secondary to the day asked."""
    for a in _items(env, "alternatives"):
        a.pop("valid", None)
        if isinstance(a.get("source"), dict):
            a["source"].pop("tier", None)
            a["source"].pop("published_on", None)


def _drop_alternative_ids(env: dict) -> None:
    for a in _items(env, "alternatives"):
        a.pop("id", None)


def _one_notice(env: dict) -> None:
    if env.get("recent_notices"):
        env["recent_notices"] = env["recent_notices"][:1]


def _drop(key: str) -> Callable[[dict], None]:
    def step(env: dict) -> None:
        env.pop(key, None)
    return step


def _drop_facts(env: dict) -> None:
    if env.get("answer"):  # the answer sentence already names the source and validity
        env.pop("facts", None)


_STEPS: list[Callable[[dict], None]] = [
    _short_titles, _compact_days, _drop_source_titles, _drop_fact_rules, _drop_fact_validity, _drop_next_answer,
    _strip_fact_notes, _drop_day_reasons, _slim_alternatives, _merge_day_runs, _one_notice, _drop("next_change"),
    _drop_alternative_ids,
]
_LAST_STEPS: list[Callable[[dict], None]] = [_strip_answer_notes, _drop("recent_notices"), _drop_facts]


def _days_in(entry: dict) -> int:
    """Days covered by a days entry (a merged run {date, to} counts every day in it)."""
    lo = dt.date.fromisoformat(entry["date"])
    return (dt.date.fromisoformat(entry.get("to", entry["date"])) - lo).days + 1


# list key -> (end that is cut first, counter). history is oldest first, so the oldest versions go first.
_LISTS = {"history": (0, "more"), "facts": (-1, "more"), "days": (-1, "more_days"), "alternatives": (-1, "more")}


def _trim_lists(env: dict, budget: int, keys: tuple[str, ...]) -> None:
    """Long lists lose items from one end and keep at least one; `more` counts the items cut, `more_days` the days."""
    for key in keys:
        end, counter = _LISTS[key]
        while approx_tokens(env) > budget and len(env.get(key) or []) > 1:
            item = env[key].pop(end)
            env[counter] = env.get(counter, 0) + (_days_in(item) if key == "days" else 1)


def fit_budget(env: dict, budget: int = BUDGET) -> dict:
    """Return a copy of env trimmed (in a fixed order) until approx_tokens() fits the budget. The asked-for lists go last:
    days lose their tail (the asked day stays first), then a conflict keeps fewer alternatives (its answer names all)."""
    out = copy.deepcopy(env)
    for step in _STEPS:
        if approx_tokens(out) <= budget:
            return out
        step(out)
    _trim_lists(out, budget, ("history", "facts"))
    for step in _LAST_STEPS:
        if approx_tokens(out) <= budget:
            return out
        step(out)
    _trim_lists(out, budget, ("days", "alternatives"))
    return out
