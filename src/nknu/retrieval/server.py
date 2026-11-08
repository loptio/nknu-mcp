"""The two read-only MCP tools (plan 2026-11-01 Task D): search(query) and read(id, part), FastMCP `nknu` (plan
2026-11-08 T4; `nknu-aca` before),
over stdio by default or streamable HTTP on 127.0.0.1 behind the Host/Origin guard. The content database is opened
read-only on every call (a rebuild replaces the file whole). An answer is one text block of compact JSON and no
structuredContent (research/mcp_tools_2026-10-29/design_note.md §5.1); a bad argument, a missing database or a bug
becomes a short error, never a traceback (the v1 patterns of nknu.server.app), and a failure says it is no proof of
查無資料 (spec 2026-11-02 §3.5). No LLM anywhere: since plan 2026-11-02 T8 search is
nknu.retrieval.hybrid.hybrid_search, whose two models only score. On a database without usable vectors of the pinned
bge-m3 (none, partial, or another model's or revision's) it is the keyword search and loads neither model (main
session 11-03); --no-dense and --no-rerank turn either model off, and --no-dense alone is the rerank-only mode, which
loads the reranker whatever the vectors (on a database built before plan 2026-11-02, without chunk_vecs, no model
loads at all). The tool texts and the handshake instructions hold for a skeleton and a pipeline build alike (T9): the
server never reads the database to pick them, and each answer's coverage line says what its build holds. Since plan
2026-11-08 T4 they name no unit and give no count (a build of many sites replaces the database while clients keep the
tool texts they read): which sites and how fresh is the answers' (coverage, data_as_of).

    python -m nknu.retrieval.server [--db research/pipeline_2026-11-02/aca.db] [--no-dense] [--no-rerank]
                                    [--http [--port 8766]]
"""

from __future__ import annotations

import argparse
import functools
import json
import logging
import sqlite3
import sys
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import quote

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError, ValidationError
from fastmcp.server.middleware import Middleware
from fastmcp.tools import ToolResult
from mcp.types import TextContent
from pydantic import ValidationError as PydanticValidationError

from nknu.core.timeutil import today_tpe
from nknu.retrieval.respond import build_response, read_response, to_json
from nknu.retrieval.types import SearchResult
from nknu.server.envelope import echo
from nknu.settings import REPO_ROOT

if TYPE_CHECKING:
    from nknu.retrieval.hybrid import HybridConfig

log = logging.getLogger(__name__)

NAME = "nknu"  # plan 2026-11-08 T4: no longer 教務處's alone ("nknu-aca" before)
DEFAULT_DB = REPO_ROOT / "research" / "pipeline_2026-11-02" / "aca.db"
DEFAULT_PORT = 8766
READ_ONLY = {"readOnlyHint": True, "openWorldHint": False}
MAX_LEN = {"query": 300, "id": 100, "part": 20}
# a failure is no proof of 查無資料 (spec 2026-11-02 §3.5, borrowed from WeKnora)
MSG_UNAVAILABLE = "資料暫時無法讀取，請稍後再試。這次失敗不代表查無資料。"
MSG_INTERNAL = "查詢失敗，請稍後再試。這次失敗不代表查無資料。"
# T9: true for a skeleton and a pipeline build alike (no build name, no format list); 換幾個說法再查 is the owner's
# 11-01 idea (spec §3.5). Owner 11-03 (addendum L): no series, so 「；同系列較舊的公告收成一行」 went from
# SEARCH_DESCRIPTION and 「；id 是 s 開頭的系列編號時列出同系列全部版本」 from READ_DESCRIPTION. Plan 2026-11-08 T4: no
# unit and no count (教務處, its calendar's start year and 「目前只收教務處網站」 went); what a build holds is in each
# answer's coverage and data_as_of
SEARCH_DESCRIPTION = ("搜尋已收錄的高師大網站的公開網頁、公告、附件與行事曆，回傳學校原文、日期與出處。"
                      "前 3 筆給全文（長文件給命中的章節和目錄），其餘給原文摘要。"
                      "query 請用繁體中文和台灣用語的關鍵字或問句"
                      "（例如用「軟體」不用「軟件」、用「資訊」不用「信息」）。"
                      "查不到或結果不對題時，請換幾個說法再查（同義詞、全稱或簡稱、學校公告的寫法），"
                      "不要只查一次就下結論。"
                      "收錄了哪些網站、資料截至何時，見每次回傳的 coverage 和 data_as_of。")
READ_DESCRIPTION = ("用 search 回傳的 id 讀一份文件：part 留空讀全文（太長給目錄和開頭），part=節號讀某一節，"
                    "part=toc 只看目錄。")
# sent at the MCP handshake, before any call (fix round 2, 11-01; plan 2026-11-02 T9 wording; plan 2026-11-08 T4: no
# unit, no site address, no year)
INSTRUCTIONS = ("高師大校園資訊：搜尋已收錄的高師大網站的公開網頁、公告、附件與行事曆；收錄範圍和資料日期見每次回傳的 "
                "coverage 與 data_as_of。回傳都是學校原文加上日期和出處，不含模型寫的句子。查詢請用繁體中文和台灣用語；"
                "結果都不能回答問題時，先換幾個說法再查；仍然查不到，請照實告訴使用者查無資料。")

SearchFn = Callable[..., SearchResult]  # nknu.retrieval.hybrid.hybrid_search(conn, query, *, today, aliases, limit)


class ArgumentError(Exception):
    """An argument the server refuses; the message (our own words) goes to the client."""


def connect(db_path: Path) -> sqlite3.Connection:
    """Read-only: nothing on the serving path can write the content database."""
    return sqlite3.connect(f"file:{quote(str(db_path))}?mode=ro", uri=True)


def aliases_of(conn: sqlite3.Connection) -> list[list[str]]:
    """The alias groups the build loaded (meta.aliases_json), so search and the build use one table."""
    row = conn.execute("SELECT value FROM meta WHERE key='aliases_json'").fetchone()
    return json.loads(row[0]) if row else []


def search_answer(db_path: Path, query: str, *, search_fn: SearchFn, today: date) -> dict:
    conn = connect(db_path)
    try:
        return build_response(conn, search_fn(conn, query, today=today, aliases=aliases_of(conn)), today=today)
    finally:
        conn.close()


def read_answer(db_path: Path, id: str, part: str, *, today: date) -> dict:
    conn = connect(db_path)
    try:
        return read_response(conn, id, part, today=today)
    finally:
        conn.close()


def _clean(value: object, name: str) -> str:
    """Lone surrogates (valid in JSON escapes, not in UTF-8) become ?; over-long arguments are refused."""
    text = str(value).encode("utf-8", "replace").decode("utf-8")
    if len(text) > MAX_LEN[name]:
        raise ArgumentError(f"{name} 最長 {MAX_LEN[name]} 字")
    return text


def _error(code: str, message: str) -> ToolResult:
    return ToolResult(content=[TextContent(type="text", text=f"錯誤 {code}：{message}")], is_error=True)


def _run(answer: Callable[[], dict]) -> ToolResult:
    try:
        resp = answer()
    except ArgumentError as exc:
        return _error("bad_argument", str(exc))
    except (OSError, sqlite3.Error):
        log.exception("content database unavailable")
        return _error("unavailable", MSG_UNAVAILABLE)
    except Exception:
        log.exception("tool failed")
        return _error("internal", MSG_INTERNAL)
    return ToolResult(content=[TextContent(type="text", text=to_json(resp))])


_WHY = (("missing", "缺少此參數"), ("unexpected", "不支援此參數"), ("extra", "不支援此參數"), ("int", "應為整數"),
        ("string", "應為字串"))


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


class ShortErrors(Middleware):
    """Argument validation runs before the tool body: its errors (and any ToolError) become short error results."""

    async def on_call_tool(self, context, call_next):
        try:
            return await call_next(context)
        except ValidationError as exc:
            return _error("bad_argument", validation_message(exc))
        except ToolError:
            return _error("internal", MSG_INTERNAL)


def default_search(config: HybridConfig | None = None) -> SearchFn:
    """nknu.retrieval.hybrid.hybrid_search, with config when one is given; imported here, so this module loads
    without it."""
    from nknu.retrieval.hybrid import hybrid_search

    return hybrid_search if config is None else functools.partial(hybrid_search, config=config)


def add_model_flags(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--no-dense", action="store_true", help="不用向量检索（bge-m3），只用关键词找候选")
    ap.add_argument("--no-rerank", action="store_true", help="不用重排模型（bge-reranker-v2-m3）")


def config_from(args: argparse.Namespace) -> HybridConfig | None:
    """HybridConfig(dense=not --no-dense, rerank=not --no-rerank) when either flag is given; else None, the
    defaults."""
    if not (args.no_dense or args.no_rerank):
        return None
    from nknu.retrieval.hybrid import HybridConfig

    return HybridConfig(dense=not args.no_dense, rerank=not args.no_rerank)


def create_mcp(db_path: Path, *, search_fn: SearchFn | None = None, today_fn: Callable[[], date] = today_tpe,
               config: HybridConfig | None = None) -> FastMCP:
    """search_fn None means default_search(config): hybrid_search, plain when config is None."""
    if search_fn is None:
        search_fn = default_search(config)
    db_path = Path(db_path)
    mcp = FastMCP(NAME, instructions=INSTRUCTIONS, mask_error_details=True, middleware=[ShortErrors()])

    @mcp.tool(name="search", description=SEARCH_DESCRIPTION, output_schema=None, annotations=READ_ONLY)
    def search(query: str) -> ToolResult:
        return _run(lambda: search_answer(db_path, _clean(query, "query"), search_fn=search_fn, today=today_fn()))

    @mcp.tool(name="read", description=READ_DESCRIPTION, output_schema=None, annotations=READ_ONLY)
    def read(id: str, part: str | int = "") -> ToolResult:
        return _run(lambda: read_answer(db_path, _clean(id, "id"), _clean(part, "part"), today=today_fn()))

    return mcp


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m nknu.retrieval.server",
                                 description="高师大已收录网站的 MCP 服务（search、read，只读）")
    ap.add_argument("--db", type=Path, default=DEFAULT_DB,
                    help="内容资料库（默认 research/pipeline_2026-11-02/aca.db）")
    add_model_flags(ap)
    ap.add_argument("--http", action="store_true", help="改用 streamable HTTP，只绑 127.0.0.1（默认 stdio）")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"HTTP 端口（默认 {DEFAULT_PORT}）")
    args = ap.parse_args(argv)
    if not args.db.is_file():
        print(f"找不到内容资料库 {args.db}；先跑 python -m nknu.content.build", file=sys.stderr)
        return 2
    mcp = create_mcp(args.db, config=config_from(args))
    if args.http:
        mcp.run(transport="http", host="127.0.0.1", port=args.port, path="/mcp", stateless_http=True,
                json_response=True, host_origin_protection=True, show_banner=False)
    else:
        mcp.run(transport="stdio", show_banner=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
