"""The MCP server: four read-only tools over serve.db (FastMCP, stateless streamable HTTP, JSON responses), a /health
route, a Host/Origin guard and a per-IP rate limit. No LLM anywhere: every answer is a prebuilt card or template.

Every tool returns an envelope (envelope.to_tool_result) or a short error (envelope.error_result); a bad argument,
an unreadable serve.db or a bug never reaches the client as a traceback. found=false queries of nknu_search and
nknu_get_hours are appended to misses.jsonl (PII masked, no client address) for the maintainer (spec §7.8)."""

from __future__ import annotations

import datetime as dt
import importlib.metadata
import json
import logging
import os
import sqlite3
import threading
from collections.abc import Callable
from pathlib import Path

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError, ValidationError
from fastmcp.server.middleware import Middleware as MCPMiddleware
from fastmcp.tools import ToolResult
from pydantic import ValidationError as PydanticValidationError
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from nknu.admin.checks import check_startup
from nknu.core.pii import redact
from nknu.core.timeutil import now_utc
from nknu.server.envelope import echo, error_result, to_tool_result
from nknu.server.query import get_calendar, get_fact, get_hours
from nknu.server.ratelimit import RateLimiter, RateLimitMiddleware
from nknu.server.search import search
from nknu.server.servedb_reader import ServeDB
from nknu.settings import Settings

log = logging.getLogger(__name__)

INSTRUCTIONS = (
    "本服務提供國立高雄師範大學（高師大）和平、燕巢兩校區的場館開放時間、教學行事曆與公告，每筆資料都帶有效期與來源；"
    "answer 是預先算好的完整句子，可直接引用。status=current 才是現行資料。stale 表示沒有現行資料，answer 給最近一筆"
    "已知資料與它的日期，轉告時要說明可能已變動；conflict（來源不一致）與 no_data（查無資料）時不得推測開放時間或日期。"
    "請照 caveat 轉告使用者，並建議洽詢句中的聯絡單位。公告只是摘要，不是核實過的事實；舊公告會標明發布日期。"
    "日期一律以台北時間計算。"
    "\n\n"
    "工具：nknu_get_hours 查某地點某天或連續幾天的開放時間；nknu_get_calendar 查行事曆事件；"
    "nknu_search 用關鍵字或問句查場館、行事曆與公告，不確定用哪個工具時先用它；"
    "nknu_get_fact 用回覆中的 id 取得單筆事實、來源與歷史版本。"
)

READ_ONLY = {"readOnlyHint": True, "openWorldHint": False}
MAX_LEN = {"query": 300, "place": 100, "keyword": 100, "id": 200}
HEALTH_MAX_AGE = dt.timedelta(hours=36)
MISSES_FILE = "misses.jsonl"
MSG_UNAVAILABLE = "資料暫時無法讀取，請稍後再試"
MSG_INTERNAL = "查詢失敗，請稍後再試"


class MissLog:
    """found=false queries, one JSON line each: {date, tool, query}. The query is PII-masked before it is cut, and no
    client address is ever recorded. A failed write is logged and never fails the tool call.

    The file is rotated by size: a line that would take it past max_bytes first moves it to misses.jsonl.1 (.1 to .2 …,
    the oldest beyond `keep` copies is dropped), so a flood of odd queries cannot fill the disk."""

    QUERY_MAX = 300
    MAX_BYTES = 5 * 1024 * 1024
    KEEP = 3

    def __init__(self, path: Path, *, max_bytes: int = MAX_BYTES, keep: int = KEEP):
        self.path = Path(path)
        self.max_bytes, self.keep = max_bytes, keep
        self._lock = threading.Lock()

    def _copy(self, n: int) -> Path:
        return self.path.with_name(f"{self.path.name}.{n}")

    def _rotate(self) -> None:
        for n in range(self.keep - 1, 0, -1):
            if self._copy(n).exists():
                os.replace(self._copy(n), self._copy(n + 1))
        os.replace(self.path, self._copy(1))

    def add(self, date: str, tool: str, query: str) -> None:
        masked = redact(query.encode("utf-8", "replace").decode("utf-8"))[: self.QUERY_MAX]
        data = (json.dumps({"date": date, "tool": tool, "query": masked}, ensure_ascii=False) + "\n").encode("utf-8")
        try:
            with self._lock:
                try:
                    size = self.path.stat().st_size
                except FileNotFoundError:
                    size = 0
                if size and size + len(data) > self.max_bytes:
                    self._rotate()
                with self.path.open("ab") as f:
                    f.write(data)
        except OSError as exc:
            log.warning("cannot append to %s: %s", self.path, exc)


def _clean(value: str | None, name: str | None = None) -> str | None:
    """Lone surrogates (valid in JSON escapes, not in UTF-8) become ?; over-long free-text arguments are refused."""
    if value is None:
        return None
    value = value.encode("utf-8", "replace").decode("utf-8")
    if name is not None and len(value) > MAX_LEN[name]:
        raise ValueError(f"{name} 最長 {MAX_LEN[name]} 字")
    return value


def _run(tool: str, answer: Callable[[], dict], misses: MissLog | None = None, query: str = "") -> ToolResult:
    try:
        env = answer()
    except OverflowError:  # e.g. 9999-12-31 plus a few days
        return error_result("bad_argument", "日期超出可查詢範圍")
    except ValueError as exc:  # the query functions' own argument checks (user input quoted through echo)
        return error_result("bad_argument", str(exc))
    except (OSError, sqlite3.Error):
        log.exception("%s: serve.db unavailable", tool)
        return error_result("unavailable", MSG_UNAVAILABLE)
    except Exception:
        log.exception("%s failed", tool)
        return error_result("internal", MSG_INTERNAL)
    if misses is not None and env.get("found") is False:
        misses.add(env["today"]["date"], tool, query)
    return to_tool_result(env)


_WHY = (("missing", "缺少此參數"), ("unexpected", "不支援此參數"), ("extra", "不支援此參數"), ("int", "應為整數"),
        ("bool", "應為 true 或 false"), ("string", "應為字串"))


def validation_message(exc: Exception) -> str:
    """The first argument error in a few words, without pydantic's text (it repeats the input and links its docs)."""
    cause = exc.__cause__
    errors = cause.errors() if isinstance(cause, PydanticValidationError) else []
    if not errors:
        return "參數不合法"
    first = errors[0]
    name = ".".join(str(x) for x in first.get("loc", ())) or "參數"
    why = next((zh for key, zh in _WHY if key in first.get("type", "")), "格式不符")
    more = f"（另有 {len(errors) - 1} 項）" if len(errors) > 1 else ""
    return f"參數不合法：{echo(name)} {why}{more}"


class ShortErrors(MCPMiddleware):
    """Argument validation runs before the tool body, so its errors (and anything FastMCP turned into a ToolError)
    become the same short error results here."""

    async def on_call_tool(self, context, call_next):
        try:
            return await call_next(context)
        except ValidationError as exc:
            return error_result("bad_argument", validation_message(exc))
        except ToolError:
            return error_result("internal", MSG_INTERNAL)


def _version() -> str:
    try:
        return importlib.metadata.version("nknu-facts")
    except importlib.metadata.PackageNotFoundError:
        return "0"


def create_mcp(db: ServeDB, *, misses_path: Path | None = None) -> FastMCP:
    """The four tools over one ServeDB; found=false queries go to misses_path (default: misses.jsonl beside serve.db)."""
    mcp = FastMCP("nknu-facts", instructions=INSTRUCTIONS, version=_version(), mask_error_details=True,
                  middleware=[ShortErrors()])
    misses = MissLog(misses_path or db.path.parent / MISSES_FILE)

    @mcp.tool(output_schema=None, annotations=READ_ONLY)
    def nknu_search(query: str, kind: str = "any", on_date: str | None = None, campus: str | None = None,
                    limit: int = 5) -> ToolResult:
        """搜尋高師大的場館時段、行事曆與公告；不確定用哪個工具時先用它。kind=any|hours|calendar|announcement|place；
        on_date=YYYY-MM-DD（預設今天）；campus=heping（和平）|yanchao（燕巢）；limit 1–10。"""
        def answer() -> dict:
            q = _clean(query, "query")
            return search(db, q, kind=kind, on_date=_clean(on_date), campus=_clean(campus), limit=limit)
        return _run("nknu_search", answer, misses, query)

    @mcp.tool(output_schema=None, annotations=READ_ONLY)
    def nknu_get_hours(place: str, date: str | None = None, days: int = 1) -> ToolResult:
        """查地點從 date 起連續 days 天（1–14）的開放時間、現在是否開放與下次變動。place 可用中文名、別名或 id，
        校區不明時列出候選；date=YYYY-MM-DD 或民國 115-10-24，預設今天。"""
        return _run("nknu_get_hours", lambda: get_hours(db, _clean(place, "place"), _clean(date), days), misses, place)

    @mcp.tool(output_schema=None, annotations=READ_ONLY)
    def nknu_get_calendar(from_date: str | None = None, to_date: str | None = None, keyword: str | None = None,
                          limit: int = 10) -> ToolResult:
        """查教學行事曆事件（開學、考試、放假、選課等）。from_date/to_date=YYYY-MM-DD，預設今天起 30 天（有 keyword 時
        180 天）；keyword 比對事件名稱；limit 1–50。"""
        return _run("nknu_get_calendar",
                    lambda: get_calendar(db, _clean(from_date), _clean(to_date), _clean(keyword, "keyword"), limit))

    @mcp.tool(output_schema=None, annotations=READ_ONLY)
    def nknu_get_fact(id: str, include_history: bool = False) -> ToolResult:
        """用回覆中的 id（事實、系列或行事曆事件）取得單筆事實、有效期與來源；include_history=true 附歷史版本。"""
        return _run("nknu_get_fact", lambda: get_fact(db, _clean(id, "id"), include_history))

    return mcp


def health(db: ServeDB, now: dt.datetime | None = None) -> tuple[int, dict]:
    """200 with the snapshot's identity; 503 when serve.db is missing or unreadable, or built over 36 hours ago."""
    try:
        meta = db.meta()
        body = {"status": "ok", "build_id": meta["build_id"], "built_at": meta["built_at"],
                "facts_version": int(meta["facts_version"])}
        built = dt.datetime.fromisoformat(meta["built_at"])
    except (OSError, sqlite3.Error, KeyError, ValueError):
        return 503, {"status": "unavailable"}
    if (now or now_utc()) - built > HEALTH_MAX_AGE:
        return 503, {**body, "status": "stale"}
    return 200, body


def startup_problems(settings: Settings, db: ServeDB) -> list[str]:
    problems = list(check_startup(settings))
    try:
        if not db.meta().get("build_id"):
            problems.append(f"startup: {db.path} has no meta.build_id; run `nknu build`")
    except (OSError, sqlite3.Error) as exc:
        problems.append(f"startup: cannot read {db.path} ({exc}); run `nknu build`")
    try:
        db.templates()
    except Exception as exc:  # noqa: BLE001 - any failure to load the templates is a refusal to start
        problems.append(f"startup: cannot load {db.templates_path} ({exc})")
    return problems


def create_app(settings: Settings) -> Starlette:
    """The ASGI app behind `nknu serve`. Refuses to start (SystemExit) when a startup check fails or serve.db is
    missing, unreadable or has no build_id."""
    db = ServeDB(settings.serve_path, templates_path=settings.config_dir / "templates.yaml")
    if problems := startup_problems(settings, db):
        raise SystemExit("\n".join(problems))
    mcp = create_mcp(db, misses_path=settings.data_dir / MISSES_FILE)

    @mcp.custom_route("/health", methods=["GET"])
    async def health_route(request: Request) -> JSONResponse:
        code, body = health(db)
        return JSONResponse(body, status_code=code)

    trust = settings.trust_cf_header  # 0 behind Tailscale Funnel: one budget for every (loopback) client
    limiter = RateLimiter(shared_loopback=not trust)
    return mcp.http_app(path="/mcp", stateless_http=True, json_response=True, host_origin_protection=True,
                        allowed_hosts=settings.allowed_hosts, allowed_origins=settings.allowed_origins,
                        middleware=[Middleware(RateLimitMiddleware, limiter=limiter, trust_cf_header=trust)])
