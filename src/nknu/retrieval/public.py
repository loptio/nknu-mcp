"""The service that faces outside (design 2026-11-21-outward-interfaces-design §二～§六): one process, one host name,
every interface a thin shell over the same two answers (server.search_answer, server.read_answer), so no two of them
can say different things and the two scoring models load once.

    /mcp                      the MCP endpoint (streamable HTTP, stateless, JSON; no login, never a 401)
    /                         the home page with the search box
    /search?q=                the results page: a search is a plain address
    /doc/<id>[?part=]         one document
    /api/search, /api/fetch   the same answers as JSON, byte for byte what the MCP tools put in their text block
    /llms.txt                 for an agent that lands on the site
    /health                   alive, and how old the library is
    anything else             404 (the /.well-known probes of a client looking for a login among them)

Around all of them: a Host allow-list (421 for any other name), the request limit of v1 (nknu.server.ratelimit: 60 a
minute per client, 600 shared by Anthropic's range), a cap on searches running at once (the models share one GPU; a
search that waits too long fails and says a failure is no proof of 查無資料), and the query log: what was searched,
what came back, what was read, by which interface; never an address, never who.

    python -m nknu.retrieval.public --db research/pipeline_2026-11-02/aca.db [--host 127.0.0.1] [--port 8770]
                                    [--log queries.jsonl] [--no-dense] [--no-rerank]

Environment (v1's names): NKNU_ALLOWED_HOSTS and NKNU_ALLOWED_ORIGINS (comma separated; the public host name and its
https origin), NKNU_TRUST_CF_HEADER (1 behind a Cloudflare tunnel, 0 behind Tailscale Funnel), NKNU_PUBLIC_URL (the
address the pages print, e.g. https://mcp.example.org; without it the request's own host), NKNU_LOCAL_PEERS (networks
whose connections count as this machine's own: in a container every request arrives from Docker's gateway, e.g.
172.16.0.0/12, and without this the limiter would take all the clients for one).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import sqlite3
import sys
import threading
import time
from collections.abc import Callable, Sequence
from contextvars import ContextVar
from datetime import date, datetime
from ipaddress import ip_address, ip_network
from pathlib import Path
from typing import TYPE_CHECKING

import anyio
from fastmcp.exceptions import ValidationError
from fastmcp.server.middleware import Middleware as ToolMiddleware
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from nknu.core.pii import redact
from nknu.core.timeutil import TAIPEI, today_tpe
from nknu.retrieval import server, web
from nknu.retrieval.respond import DEFAULT_DETAIL, DETAILS, to_json
from nknu.server.ratelimit import RateLimiter, RateLimitMiddleware

if TYPE_CHECKING:
    from starlette.applications import Starlette

    from nknu.retrieval.hybrid import HybridConfig

log = logging.getLogger(__name__)

DEFAULT_PORT = 8770
LOCAL_HOSTS = ("127.0.0.1", "localhost", "[::1]")
MAX_SEARCHES = 4      # searches running at once; the rest wait 【Claude, by judgement】
WAIT_SECONDS = 20.0   # a search that waits longer fails 【Claude, by judgement】
STALE_HOURS = 36      # /health says stale past this: a night's update did not land
SORTS = ("relevance", "date")
MSG_BUSY = "現在查詢的人比較多，請稍後再試。這次失敗不代表查無資料。"
JSON_UTF8 = "application/json; charset=utf-8"
NO_INDEX = {"X-Robots-Tag": "noindex"}  # pages and answers that carry the school's text stay out of search engines
OPEN = {"Access-Control-Allow-Origin": "*"}  # public read-only data, no credentials: any page's script may read it
ROBOTS = "User-agent: *\nAllow: /\n"


# why a call got no answer, as the query log says it (the panel reads these: nknu.ops)
BUSY, BAD_ARGS, UNAVAILABLE, INTERNAL = "busy", "bad_args", "unavailable", "internal"
TOOL_ERRORS = {"bad_argument": BAD_ARGS, "unavailable": UNAVAILABLE, "internal": INTERNAL}  # server._error's codes
LOGGED_ARGS = ("query", "detail", "limit", "unit", "sort", "id", "part")
# what an MCP tool call notes for its middleware: the tool body runs in a worker thread with a copy of the context,
# and the copy holds this same dict
_CALL: ContextVar[dict | None] = ContextVar("nknu_public_call", default=None)


class Busy(Exception):
    """Too many searches at once and this one waited WAIT_SECONDS."""


def ms_since(start: float) -> int:
    return round((time.monotonic() - start) * 1000)


class Gate:
    """At most `most` searches run at once; one more waits up to `wait` seconds, then Busy."""

    def __init__(self, most: int = MAX_SEARCHES, wait: float = WAIT_SECONDS):
        self._slots, self.wait = threading.BoundedSemaphore(most), wait

    def __call__(self, search_fn: server.SearchFn) -> server.SearchFn:
        def gated(*args, **kwargs):
            if not self._slots.acquire(timeout=self.wait):
                if (note := _CALL.get()) is not None:
                    note["busy"] = True
                raise Busy
            try:
                return search_fn(*args, **kwargs)
            finally:
                self._slots.release()

        return gated


class QueryLog:
    """One JSON line a call: the time (Asia/Taipei), the interface (mcp, web, api), the tool, its arguments, whether
    anything was found, the ids that came back, and ms: the milliseconds from the call's arrival to its answer, the
    wait for a search slot included. A call that got no answer has a line too, with error (busy: it waited
    WAIT_SECONDS for a slot; bad_args; unavailable: the library could not be read; internal), found false and no ids.
    The searched text is masked for personal data (nknu.core.pii) and cut; no client address is ever written. Rotated
    by size (v1's MissLog); a failed write is logged and never fails the call. path None: nothing is written."""

    TEXT_MAX = 300
    MAX_BYTES = 20 * 1024 * 1024
    KEEP = 5

    def __init__(self, path: Path | None, *, max_bytes: int = MAX_BYTES, keep: int = KEEP,
                 now: Callable[[], datetime] = lambda: datetime.now(TAIPEI)):
        self.path = Path(path) if path else None
        self.max_bytes, self.keep, self.now = max_bytes, keep, now
        self._lock = threading.Lock()

    def _copy(self, n: int) -> Path:
        return self.path.with_name(f"{self.path.name}.{n}")

    def _rotate(self) -> None:
        for n in range(self.keep - 1, 0, -1):
            if self._copy(n).exists():
                os.replace(self._copy(n), self._copy(n + 1))
        os.replace(self.path, self._copy(1))

    def add(self, via: str, tool: str, args: dict, resp: dict | None = None, *, ms: int | None = None,
            error: str = "") -> None:
        """resp None: the call got no answer (error says why). Of the arguments only the tools' own are written."""
        if self.path is None:
            return
        resp = resp or {}
        said = {k: redact(v.encode("utf-8", "replace").decode("utf-8"))[: self.TEXT_MAX] if isinstance(v, str) else v
                for k in LOGGED_ARGS if isinstance(v := args.get(k), str | int | float) and v not in ("", None)}
        ids = [r.get("id") for r in resp.get("results", [])] if tool == "search" else [resp.get("id")]
        line = {"t": self.now().isoformat(timespec="seconds"), "via": via, "tool": tool, **said,
                "found": bool(resp.get("found")), "ids": [i for i in ids if i],
                **({"ms": ms} if ms is not None else {}), **({"error": error} if error else {})}
        data = (json.dumps(line, ensure_ascii=False) + "\n").encode("utf-8")
        try:
            with self._lock:
                size = self.path.stat().st_size if self.path.exists() else 0
                if size and size + len(data) > self.max_bytes:
                    self._rotate()
                with self.path.open("ab") as f:
                    f.write(data)
        except OSError as exc:
            log.warning("查询纪录写不进 %s：%s", self.path, exc)


class LogCalls(ToolMiddleware):
    """The MCP side of the query log: every tool call, answered or not, with how long it took. A search that waited
    too long for a slot answers with MSG_BUSY (server._run alone would call it an internal failure)."""

    def __init__(self, qlog: QueryLog):
        self.qlog = qlog

    def _add(self, context, start: float, resp: dict | None, error: str = "") -> None:
        with contextlib.suppress(Exception):  # the log never fails a call
            self.qlog.add("mcp", context.message.name, dict(context.message.arguments or {}), resp,
                          ms=ms_since(start), error=error)

    async def on_call_tool(self, context, call_next):
        note: dict = {}
        token, start = _CALL.set(note), time.monotonic()
        try:
            result = await call_next(context)
        except Exception as exc:  # a refused argument, when this middleware sits inside the one that words it
            self._add(context, start, None, BAD_ARGS if isinstance(exc, ValidationError) else INTERNAL)
            raise
        finally:
            _CALL.reset(token)
        if note.get("busy"):
            self._add(context, start, None, BUSY)
            return server._error(UNAVAILABLE, MSG_BUSY)
        text = result.content[0].text if result.content else ""
        try:
            resp, error = json.loads(text), ""
        except ValueError:  # an error result: 「錯誤 <code>：…」 (server._error)
            resp, error = None, TOOL_ERRORS.get(text[3:].split("：", 1)[0].strip(), INTERNAL)
        self._add(context, start, resp if isinstance(resp, dict) else None, error)
        return result


class HostGuard:
    """Every path answers only under a name on the list (421 otherwise): a page of another site that reaches the port
    under a name of its own gets nothing. FastMCP's own guard covers /mcp; this one covers the pages too."""

    def __init__(self, app: ASGIApp, allowed: Sequence[str]):
        self.app, self.allowed = app, {h.lower() for h in (*LOCAL_HOSTS, *allowed)}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            host = dict(scope["headers"]).get(b"host", b"").decode("latin-1").lower()
            name = host if host.endswith("]") else host.rsplit(":", 1)[0] if ":" in host else host
            if name not in self.allowed:
                await PlainTextResponse("Misdirected Request", status_code=421)(scope, receive, send)
                return
        await self.app(scope, receive, send)


class LocalPeer:
    """A connection from one of these networks is this machine's own (its peer becomes 127.0.0.1). In a container the
    tunnel on the host reaches the service through Docker's gateway, so every request has the gateway's address:
    v1's limiter (nknu.server.ratelimit) trusts the tunnel's client header, or shares one budget, only for a loopback
    peer. Only the published port's forwarder can connect from there (Docker publishes it on the host's 127.0.0.1)."""

    def __init__(self, app: ASGIApp, networks: Sequence[str]):
        self.app, self.nets = app, tuple(ip_network(n) for n in networks)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and self.nets and (client := scope.get("client")):
            try:
                addr = ip_address(client[0])
            except ValueError:
                addr = None
            if addr is not None and any(addr.version == n.version and addr in n for n in self.nets):
                scope = {**scope, "client": ("127.0.0.1", client[1])}
        await self.app(scope, receive, send)


def _csv(value: str | None) -> list[str]:
    return [p.strip() for p in (value or "").split(",") if p.strip()]


def stats(db_path: Path) -> dict:
    """What /health and the home page say of the library: when it was built, its documents, the newest 資料截至 of
    its sites. Raises OSError or sqlite3.Error when it cannot be read."""
    conn = server.connect(db_path)
    try:
        meta = dict(conn.execute("SELECT key, value FROM meta WHERE key IN ('built_at', 'data_as_of')"))
        docs = conn.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
        as_of = meta.get("data_as_of")
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sites'").fetchone():
            as_of = conn.execute("SELECT MAX(data_as_of) FROM sites").fetchone()[0] or as_of
        return {"built_at": meta.get("built_at"), "docs": docs, "data_as_of": as_of}
    finally:
        conn.close()


def health(db_path: Path, now: datetime | None = None) -> tuple[int, dict]:
    """200 while the library can be read, with its age; stale (still 200: the service answers, with older data) when
    it was built over STALE_HOURS ago. 503 only when it cannot be read."""
    try:
        s = stats(db_path)
    except (OSError, sqlite3.Error):
        return 503, {"status": "unavailable"}
    body = {"status": "ok", **s}
    try:
        built = datetime.fromisoformat(s["built_at"])
        hours = ((now or datetime.now(TAIPEI)) - built).total_seconds() / 3600
        body |= {"age_hours": round(hours, 1), "stale": hours > STALE_HOURS}
    except (TypeError, ValueError):
        pass
    return 200, body


def search_args(params) -> dict:
    """The search arguments of a page or API address, checked as the MCP tool checks them (the same words when one
    is refused). Raises server.ArgumentError."""
    q = server._clean(params.get("q", params.get("query", "")), "query").strip()
    if not q:
        raise server.ArgumentError("參數不合法：q 缺少此參數")
    detail = params.get("detail", DEFAULT_DETAIL)
    if detail not in DETAILS:
        raise server.ArgumentError("參數不合法：detail 只能填 'snippet', 'passage' or 'full'")
    sort = params.get("sort", "relevance")
    if sort not in SORTS:
        raise server.ArgumentError("參數不合法：sort 只能填 'relevance' or 'date'")
    try:
        limit = int(params.get("limit", server.DEFAULT_LIMIT))
    except ValueError:
        limit = 0
    if not 1 <= limit <= server.MAX_LIMIT:
        raise server.ArgumentError("參數不合法：limit 應為 1～50 的整數")
    return {"query": q, "detail": detail, "limit": limit, "unit": server._clean(params.get("unit", ""), "unit").strip(),
            "sort": sort}


def base_url(request: Request, public_url: str) -> str:
    """The address the pages print: NKNU_PUBLIC_URL, else the request's own host (https unless it is this machine)."""
    if public_url:
        return public_url.rstrip("/")
    host = request.headers.get("host", "127.0.0.1")
    local = host.split(":")[0] in ("127.0.0.1", "localhost") or host.startswith("[::1]")
    return f"{'http' if local else 'https'}://{host}"


def create_app(db_path: Path, *, search_fn: server.SearchFn | None = None, today_fn: Callable[[], date] = today_tpe,
               config: HybridConfig | None = None, allowed_hosts: Sequence[str] = (),
               allowed_origins: Sequence[str] = (), trust_cf_header: bool = True, log_path: Path | None = None,
               public_url: str = "", gate: Gate | None = None, limiter: RateLimiter | None = None,
               local_peers: Sequence[str] = ()) -> Starlette:
    """The ASGI app. search_fn None: server.default_search(config). One gated search serves the MCP tools and the
    pages alike."""
    db_path = Path(db_path)
    search = (gate or Gate())(search_fn or server.default_search(config))
    qlog = QueryLog(log_path)
    texts = server.texts_for(db_path)
    mcp = server.create_mcp(db_path, search_fn=search, today_fn=today_fn)
    mcp.add_middleware(LogCalls(qlog))

    async def answer(fn: Callable[[], dict]) -> tuple[dict | None, int, str, str]:
        """(the answer, the HTTP status, the message when there is no answer, why not as the query log says it)."""
        try:
            return await anyio.to_thread.run_sync(fn), 200, "", ""
        except server.ArgumentError as exc:
            return None, 400, str(exc), BAD_ARGS
        except Busy:
            return None, 503, MSG_BUSY, BUSY
        except (OSError, sqlite3.Error):
            log.exception("content database unavailable")
            return None, 503, server.MSG_UNAVAILABLE, UNAVAILABLE
        except Exception:
            log.exception("search or fetch failed")
            return None, 500, server.MSG_INTERNAL, INTERNAL

    async def searched(request: Request, via: str) -> tuple[dict | None, int, str, dict]:
        start = time.monotonic()
        try:
            args = search_args(request.query_params)
        except server.ArgumentError as exc:
            if (q := request.query_params.get("q", "")).strip():  # an address without q is no query
                qlog.add(via, "search", {"query": q}, ms=ms_since(start), error=BAD_ARGS)
            return None, 400, str(exc), {}
        resp, status, message, error = await answer(lambda: server.search_answer(
            db_path, args["query"], search_fn=search, today=today_fn(), detail=args["detail"], limit=args["limit"],
            unit=args["unit"], sort=args["sort"]))
        qlog.add(via, "search", args, resp, ms=ms_since(start), error=error)
        return resp, status, message, args

    async def fetched(doc_id: str, part: str, via: str) -> tuple[dict | None, int, str]:
        start = time.monotonic()

        def read() -> dict:
            return server.read_answer(db_path, server._clean(doc_id, "id"), server._clean(part, "part"),
                                      today=today_fn())

        resp, status, message, error = await answer(read)
        qlog.add(via, "fetch", {"id": doc_id, "part": part}, resp, ms=ms_since(start), error=error)
        return resp, status, message

    def as_json(resp: dict | None, status: int, message: str) -> Response:
        if resp is None:
            code = "bad_argument" if status == 400 else "unavailable" if status == 503 else "internal"
            return JSONResponse({"error": code, "message": message}, status_code=status, headers=NO_INDEX | OPEN)
        return Response(to_json(resp), media_type=JSON_UTF8, headers=NO_INDEX | OPEN)

    @mcp.custom_route("/health", methods=["GET"])
    async def health_route(request: Request) -> Response:
        code, body = await anyio.to_thread.run_sync(health, db_path)
        return JSONResponse(body, status_code=code)

    @mcp.custom_route("/", methods=["GET"])
    async def home_route(request: Request) -> Response:
        try:
            s = await anyio.to_thread.run_sync(stats, db_path)
        except (OSError, sqlite3.Error):
            s = {}
        base = base_url(request, public_url)
        return HTMLResponse(web.home_html(texts.instructions, mcp_url=f"{base}/mcp", base=base, docs=s.get("docs"),
                                          as_of=s.get("data_as_of")))

    @mcp.custom_route("/search", methods=["GET"])
    async def search_route(request: Request) -> Response:
        params = request.query_params
        q = params.get("q", "")
        if not q.strip():
            return HTMLResponse(web.search_html(None, "", message="請輸入要查的內容。"), headers=NO_INDEX)
        resp, status, message, args = await searched(request, "web")
        html = web.search_html(resp, q, unit=args.get("unit", params.get("unit", "")),
                               sort=args.get("sort", "relevance"), message=message)
        return HTMLResponse(html, status_code=status, headers=NO_INDEX)

    @mcp.custom_route("/doc/{doc_id:path}", methods=["GET"])
    async def doc_route(request: Request) -> Response:
        doc_id, part = request.path_params["doc_id"], request.query_params.get("part", "")
        resp, status, message = await fetched(doc_id, part, "web")
        if resp is None:
            return HTMLResponse(web.search_html(None, "", message=message), status_code=status, headers=NO_INDEX)
        return HTMLResponse(web.doc_html(resp, doc_id, part), status_code=200 if resp.get("found") else 404,
                            headers=NO_INDEX)

    @mcp.custom_route("/api/search", methods=["GET"])
    async def api_search_route(request: Request) -> Response:
        resp, status, message, _ = await searched(request, "api")
        return as_json(resp, status, message)

    @mcp.custom_route("/api/fetch", methods=["GET"])
    async def api_fetch_route(request: Request) -> Response:
        doc_id = request.query_params.get("id", "")
        if not doc_id.strip():
            return as_json(None, 400, "參數不合法：id 缺少此參數")
        return as_json(*await fetched(doc_id, request.query_params.get("part", ""), "api"))

    @mcp.custom_route("/llms.txt", methods=["GET"])
    async def llms_route(request: Request) -> Response:
        return PlainTextResponse(web.llms_txt(texts.instructions, base=base_url(request, public_url)))

    @mcp.custom_route("/robots.txt", methods=["GET"])
    async def robots_route(request: Request) -> Response:
        return PlainTextResponse(ROBOTS)

    limiter = limiter or RateLimiter(shared_loopback=not trust_cf_header)
    return mcp.http_app(path="/mcp", stateless_http=True, json_response=True, host_origin_protection=True,
                        allowed_hosts=list(allowed_hosts), allowed_origins=list(allowed_origins),
                        middleware=[Middleware(HostGuard, allowed=tuple(allowed_hosts)),
                                    Middleware(LocalPeer, networks=tuple(local_peers)),
                                    Middleware(RateLimitMiddleware, limiter=limiter, trust_cf_header=trust_cf_header)])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m nknu.retrieval.public",
                                 description="对外的服务：MCP、网页、HTTP 接口，同一个进程（只读）")
    ap.add_argument("--db", type=Path, default=server.DEFAULT_DB, help="内容资料库")
    server.add_model_flags(ap)
    ap.add_argument("--host", default="127.0.0.1", help="听的位址：默认只有本机；容器里用 0.0.0.0，由 Docker 只从主机的"
                                                        " 127.0.0.1 转进来")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"端口（默认 {DEFAULT_PORT}）")
    ap.add_argument("--log", type=Path, default=None, help="查询纪录写到哪个档（不给就不记）")
    args = ap.parse_args(argv)
    if not args.db.is_file():
        print(f"找不到内容资料库 {args.db}", file=sys.stderr)
        return 2
    import uvicorn

    env = os.environ
    hosts = _csv(env.get("NKNU_ALLOWED_HOSTS"))
    app = create_app(args.db, config=server.config_from(args), allowed_hosts=hosts,
                     allowed_origins=_csv(env.get("NKNU_ALLOWED_ORIGINS")),
                     trust_cf_header=env.get("NKNU_TRUST_CF_HEADER", "1") != "0", log_path=args.log,
                     public_url=env.get("NKNU_PUBLIC_URL", ""), local_peers=_csv(env.get("NKNU_LOCAL_PEERS")))
    print(f"对外服务：http://{args.host}:{args.port}/ （MCP 在 /mcp；认的主机名：{', '.join([*LOCAL_HOSTS, *hosts])}；"
          f"查询纪录：{args.log or '不记'}）", file=sys.stderr)
    # proxy_headers off: the limiter must see the real TCP peer (nknu.server.ratelimit)
    uvicorn.run(app, host=args.host, port=args.port, proxy_headers=False, server_header=False, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
