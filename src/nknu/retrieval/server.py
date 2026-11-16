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
loads at all).

What an agent is told before its first call (design 2026-11-16-mcp-interface-design §八): the handshake instructions
and the search tool's description are built once, when the server starts, from the database's sites and unit tree
(texts_for): what is collected and what is not, and which offices, colleges, departments and centres are in. The owner
11-16: an agent that knows nothing of the school 「不知道 这个网站提供哪些资料 要去查哪些」; 11-17 「先做第一部分」.
Plan 2026-11-08 T4 had kept units and counts out of these texts because a rebuild replaces the database while clients
keep what they read; a nightly rebuild does not change the unit tree, and a build that adds sites or units (round 2)
is followed by a restart of the service. A database without sites or unit rows (the skeleton, a test database) keeps
the fixed texts of then (INSTRUCTIONS, SEARCH_DESCRIPTION).

    python -m nknu.retrieval.server [--db research/pipeline_2026-11-02/aca.db] [--no-dense] [--no-rerank]
                                    [--http [--host 127.0.0.1] [--port 8766]]
"""

from __future__ import annotations

import argparse
import functools
import json
import logging
import sqlite3
import sys
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
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
from nknu.retrieval.respond import CALENDAR_PART, build_response, read_response, to_json
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
SEARCH_HOW = ("前 3 筆給全文（長文件給命中的章節和目錄），其餘給原文摘要。"
              "query 請用繁體中文和台灣用語的關鍵字或問句"
              "（例如用「軟體」不用「軟件」、用「資訊」不用「信息」）。"
              "查不到或結果不對題時，請換幾個說法再查（同義詞、全稱或簡稱、學校公告的寫法），"
              "不要只查一次就下結論。")
SEARCH_DESCRIPTION = ("搜尋已收錄的高師大網站的公開網頁、公告、附件與行事曆，回傳學校原文、日期與出處。"
                      + SEARCH_HOW + "收錄了哪些網站、資料截至何時，見每次回傳的 coverage 和 data_as_of。")
READ_DESCRIPTION = ("用 search 回傳的 id 讀一份文件：part 留空讀全文（太長給目錄和開頭），part=節號讀某一節，"
                    "part=toc 只看目錄。")
# sent at the MCP handshake, before any call (fix round 2, 11-01; plan 2026-11-02 T9 wording; plan 2026-11-08 T4: no
# unit, no site address, no year)
INSTRUCTIONS = ("高師大校園資訊：搜尋已收錄的高師大網站的公開網頁、公告、附件與行事曆；收錄範圍和資料日期見每次回傳的 "
                "coverage 與 data_as_of。回傳都是學校原文加上日期和出處，不含模型寫的句子。查詢請用繁體中文和台灣用語；"
                "結果都不能回答問題時，先換幾個說法再查；仍然查不到，請照實告訴使用者查無資料。")

# design 2026-11-16-mcp-interface-design §八: the texts built from the database (texts_for). Wording 【Claude】. The two
# examples of the school's wording are alias groups whose comments say which word the site writes
# (config/aliases.yaml: 學雜費 Q35, 交通車 N01). 不是學校的官方服務: the service is the owner's, not the school's.
ORIENT_HEAD = "高師大校園資訊：整理自高師大 {sites} 個網站的公開資料，不是學校的官方服務。"
ORIENT_IN = "收錄：各單位網站的公開網頁、公告與附件（PDF、Word、Excel 等；掃描檔和圖片裡的字由程式辨識，可能有錯字）{calendar}。"
ORIENT_OUT = "沒有收錄：要登入的系統（選課、成績、校務行政、數位學習平台）、社群平台和校外網站的內容。"
ORIENT_SOURCE = "回傳都是學校原文加上日期和出處，不含模型寫的句子；每筆的 data_as_of 是那個網站的資料截至日。"
ORIENT_UNITS = "收錄的單位："
ORIENT_ADMIN = "行政單位（括號裡是底下的組）：{units}。"
ORIENT_COLLEGES = "學院和系所：{colleges}。"
ORIENT_COLLEGE = "{name}（{campus}校區）：{depts}"
ORIENT_COLLEGE_NO_CAMPUS = "{name}：{depts}"
ORIENT_OTHER_DEPTS = "其他系所和學程：{depts}。"
ORIENT_CENTRES = "中心：{units}。"
ORIENT_HOW = ("查詢：請用繁體中文和台灣用語的關鍵字或問句。學校的寫法常和口語不同（例如學校寫「學雜費」不寫「學費」、"
              "寫「交通車」不寫「校車」）；問某個單位的事，查詢裡寫上單位名稱。結果都不能回答問題時，先換幾個說法再查；"
              "仍然查不到，請照實告訴使用者查無資料，不要推測。")
SEARCH_WHAT = ("搜尋高師大 {sites} 個網站已收錄的公開網頁、公告、附件{calendar}，回傳學校原文、日期與出處。"
               "收錄的單位有：{admin}等行政單位，{colleges}這 {n_colleges} 個學院的 {n_depts} 個系所，"
               "以及 {n_centres} 個中心；要登入的系統和校外網站的內容沒有收錄。")
SEARCH_FRESH = "各網站的資料截至日見每筆的 data_as_of。"
ADMIN, GROUP, COLLEGE, CENTRE = "行政单位", "行政组别", "学院", "中心"  # nknu.content.units.KINDS
DEPT_KINDS = frozenset({"系所", "学程"})

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


@dataclass(frozen=True)
class Texts:
    """What an agent reads before its first call: the handshake instructions and the search tool's description."""

    instructions: str
    search: str


FIXED = Texts(INSTRUCTIONS, SEARCH_DESCRIPTION)


@dataclass(frozen=True)
class _Unit:
    unit_id: str
    kind: str
    parent_id: str | None
    campus: str | None
    docs: int  # its own documents and those of every unit below it


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _units(conn: sqlite3.Connection) -> list[_Unit]:
    """The unit tree's units that hold documents (their own or a unit's below them), most documents first, then by
    name: a unit with none is left out, so the texts never name a unit the database has nothing of."""
    rows = conn.execute("SELECT unit_id, kind, parent_id, campus FROM units").fetchall()
    parent = {r[0]: r[2] for r in rows}
    total: Counter[str] = Counter()
    for unit, n in conn.execute("SELECT unit, COUNT(*) FROM docs WHERE unit IS NOT NULL GROUP BY unit"):
        seen: set[str] = set()
        while unit in parent and unit not in seen:  # a unit not in the tree (written as the page says) is skipped
            seen.add(unit)
            total[unit] += n
            unit = parent[unit]
    # most of an office's documents name the office as their unit and the group as their grp (「單位＝處、組別＝組」,
    # 11-15): a group counts the documents of its office that name it; its office already counts them
    for grp, unit, n in conn.execute("SELECT grp, unit, COUNT(*) FROM docs WHERE grp IS NOT NULL AND grp <> ''"
                                     " GROUP BY grp, unit"):
        if grp != unit and parent.get(grp) == unit:
            total[grp] += n
    units = [_Unit(r[0], r[1], r[2], r[3], total[r[0]]) for r in rows if total[r[0]]]
    return sorted(units, key=lambda u: (-u.docs, u.unit_id))


def _below(units: list[_Unit], top: str, kinds: frozenset[str] | set[str]) -> list[_Unit]:
    """The units of these kinds anywhere below top, in the units' order (a 碩士班 under its 學系 is the college's)."""
    parent = {u.unit_id: u.parent_id for u in units}

    def under(u: _Unit) -> bool:
        p, seen = u.parent_id, set()
        while p is not None and p not in seen:
            if p == top:
                return True
            seen.add(p)
            p = parent.get(p)
        return False

    return [u for u in units if u.kind in kinds and under(u)]


def _texts(conn: sqlite3.Connection) -> Texts | None:
    """design 2026-11-16-mcp-interface-design §八: the instructions and the search description from the sites table
    and the unit tree; None when the database has neither sites nor units with documents."""
    if not (_has_table(conn, "sites") and _has_table(conn, "units")):
        return None
    sites = conn.execute("SELECT COUNT(*) FROM sites").fetchone()[0]
    units = _units(conn)
    if not sites or not units:
        return None
    events = conn.execute("SELECT 1 FROM docs WHERE kind_class='event' LIMIT 1").fetchone() is not None
    calendar = CALENDAR_PART if events else ""
    admin = [u for u in units if u.kind == ADMIN]
    colleges = [u for u in units if u.kind == COLLEGE]
    centres = [u for u in units if u.kind == CENTRE]
    depts = [u for u in units if u.kind in DEPT_KINDS]
    in_college = {d.unit_id for c in colleges for d in _below(units, c.unit_id, DEPT_KINDS)}

    def office(u: _Unit) -> str:
        groups = [g.unit_id for g in units if g.kind == GROUP and g.parent_id == u.unit_id]
        return u.unit_id + (f"（{'、'.join(groups)}）" if groups else "")

    def college(u: _Unit) -> str:
        names = "、".join(d.unit_id for d in _below(units, u.unit_id, DEPT_KINDS)) or "（沒有系所的資料）"
        if u.campus:
            return ORIENT_COLLEGE.format(name=u.unit_id, campus=u.campus, depts=names)
        return ORIENT_COLLEGE_NO_CAMPUS.format(name=u.unit_id, depts=names)

    lines = [ORIENT_HEAD.format(sites=sites), ORIENT_IN.format(calendar=calendar), ORIENT_OUT, ORIENT_SOURCE, "",
             ORIENT_UNITS]
    if admin:
        lines.append(ORIENT_ADMIN.format(units="、".join(office(u) for u in admin)))
    if colleges:
        lines.append(ORIENT_COLLEGES.format(colleges="；".join(college(u) for u in colleges)))
    if other := [d.unit_id for d in depts if d.unit_id not in in_college]:
        lines.append(ORIENT_OTHER_DEPTS.format(depts="、".join(other)))
    if centres:
        lines.append(ORIENT_CENTRES.format(units="、".join(u.unit_id for u in centres)))
    lines += ["", ORIENT_HOW]
    search = (SEARCH_WHAT.format(sites=sites, calendar=calendar, admin="、".join(u.unit_id for u in admin),
                                 colleges="、".join(u.unit_id for u in colleges), n_colleges=len(colleges),
                                 n_depts=len(depts), n_centres=len(centres))
              + SEARCH_HOW + SEARCH_FRESH)
    return Texts("\n".join(lines), search)


def texts_for(db_path: Path) -> Texts:
    """The texts for this database (_texts), read once when the server starts; FIXED when it has no sites or unit
    rows, or cannot be read (a missing database still starts a server whose calls answer 資料暫時無法讀取)."""
    try:
        conn = connect(Path(db_path))
    except sqlite3.Error:
        log.warning("读不了内容资料库，服务说明用固定的文字：%s", db_path)
        return FIXED
    try:
        return _texts(conn) or FIXED
    except sqlite3.Error:
        log.exception("读不了单位树，服务说明用固定的文字")
        return FIXED
    finally:
        conn.close()


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
    texts = texts_for(db_path)
    mcp = FastMCP(NAME, instructions=texts.instructions, mask_error_details=True, middleware=[ShortErrors()])

    @mcp.tool(name="search", description=texts.search, output_schema=None, annotations=READ_ONLY)
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
    ap.add_argument("--http", action="store_true", help="改用 streamable HTTP，默认只绑 127.0.0.1（默认 stdio）")
    # plan 2026-11-15 T6: in the lab's Docker container the server listens on every address of the container and Docker
    # forwards only the lab PC's own 127.0.0.1 to it (as the archive viewer does). The guard below is strict wherever
    # it listens: a request is answered only when its Host is 127.0.0.1, localhost, [::1] or the address the
    # connection itself came in on (FastMCP adds that one: fastmcp/server/http.py, _allowed_hosts_for_scope), so a
    # page of another site that reaches the port under a name of its own gets none. Who can connect at all is the
    # caller's to limit (Docker's 127.0.0.1 publish): 0.0.0.0 outside a container answers the LAN by its address
    ap.add_argument("--host", default="127.0.0.1",
                    help="HTTP 模式听的位址：默认 127.0.0.1，只有本机连得到；实验室的 Docker 容器里用 0.0.0.0，由 Docker 只从"
                         "主机自己的 127.0.0.1 转进来。不管听哪里，别的网站借浏览器用它自己的名字连进来一律不回答；"
                         "在容器外面用 0.0.0.0 会让同一个网路的机器连得到，不要这样用")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"HTTP 端口（默认 {DEFAULT_PORT}）")
    args = ap.parse_args(argv)
    if not args.db.is_file():
        print(f"找不到内容资料库 {args.db}；先跑 python -m nknu.content.build", file=sys.stderr)
        return 2
    mcp = create_mcp(args.db, config=config_from(args))
    if args.http:
        mcp.run(transport="http", host=args.host, port=args.port, path="/mcp", stateless_http=True,
                json_response=True, host_origin_protection=True, show_banner=False)
    else:
        mcp.run(transport="stdio", show_banner=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
