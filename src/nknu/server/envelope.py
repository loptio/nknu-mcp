"""The output envelope shared by every tool. structuredContent IS the payload for the model; the text block is rendered
from the same dict by render_text (Claude Code / VS Code / Codex read structuredContent, Gemini CLI reads text).

Answers are never trimmed (U1, 2026-10-23: the spec §8 400-token budget and its trim ladder are gone): an envelope
carries every sentence, fact, day and alternative it was built with. What stays bounded is what the caller asks for
(limit, days), the length of free-text arguments (app.MAX_LEN) and errors: an error stays within ERROR_BUDGET o200k
tokens, checked without tiktoken (a dev dependency) by approx_tokens(), a conservative estimate."""

from __future__ import annotations

import json
import math
import re

from fastmcp.tools import ToolResult
from mcp.types import TextContent

NOTICE_LABEL = "公告摘要，非核實事實"
ERROR_BUDGET = 80
ECHO_BYTES = 24  # user input quoted in an error message (echo)
ERROR_MESSAGE_MAX = 120


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
    "from": "自", "date": "日期", "weekday": "星期",
    "time": "時間", "open": "開放", "segments": "時段", "reason": "原因", "label": "標示", "summary": "事件",
    "category": "類別", "recorded_from": "收錄於", "recorded_to": "取代於", "superseded_by": "現行版本",
    "code": "代碼", "message": "說明", "unit": "單位",
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


def echo(value: object) -> str:
    """User input quoted inside an error message. Non-printable characters become ?, and the quote is cut to ECHO_BYTES
    of UTF-8: a token is at least one byte, so this bounds its real token count, which approx_tokens() can undercount
    3x for rare characters (㐀 is 3 tokens). Every piece of user input reaches an error message through here."""
    s = "".join(c if c.isprintable() else "?" for c in str(value))
    size = 0
    for i, c in enumerate(s):
        size += len(c.encode())
        if size > ECHO_BYTES:
            return f"「{s[:i]}…」"
    return f"「{s}」"


def _error_text(code: str, message: str) -> str:
    return f"錯誤 {code}：{message}"


def _error_fits(code: str, message: str) -> bool:
    limit = ERROR_BUDGET - ECHO_BYTES  # room for one echo() whose tokens approx_tokens() undercounts
    return max(approx_tokens(_error_text(code, message)), approx_tokens({"code": code, "message": message})) <= limit


def error_result(code: str, message: str) -> ToolResult:
    """A short, instruction-free error (Gemini CLI hands isError JSON to the model unwrapped). The message (our own
    wording plus at most one echo()) is cut until both forms fit ERROR_BUDGET."""
    msg = message[:ERROR_MESSAGE_MAX]
    if msg != message or not _error_fits(code, msg):
        cut = msg
        while cut and not _error_fits(code, cut + "…"):
            cut = cut[:-1]
        msg = cut + "…"
    body = {"code": code, "message": msg}
    return ToolResult(content=[TextContent(type="text", text=_error_text(code, msg))], structured_content=body,
                      is_error=True)


# ---------------------------------------------------------------- token counts (error budget)

def _dump(obj: object) -> str:
    return obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=str)


def count_tokens(obj: object) -> int:
    """Exact o200k_base count of obj as sent (compact JSON, or the string itself). Tests only: tiktoken is a dev dependency."""
    import tiktoken

    return len(tiktoken.get_encoding("o200k_base").encode(_dump(obj)))


# Pre-tokens split the way o200k does (letter runs incl. CJK with one leading symbol, digit groups of <=3, symbol runs).
# The per-class weights were fitted (non-negative least squares) on ~13k envelopes from the fixture worlds, then scaled
# by 1.12 so the estimate stayed 1.03-1.19x the real count there. Only errors are bounded now; the error tests check
# the real count of the cut messages.
_HAN = "㐀-鿿豈-﫿"
_PRE = re.compile(rf"[^\r\n A-Za-z0-9{_HAN}]?[A-Za-z{_HAN}]+|\d{{1,3}}| ?[^\sA-Za-z0-9{_HAN}]+|\s+")
_HAN_CHAR = re.compile(f"[{_HAN}]")


def approx_tokens(obj: object) -> int:
    """Upper estimate of count_tokens() without a tokenizer, for cutting error messages on the serving path."""
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
