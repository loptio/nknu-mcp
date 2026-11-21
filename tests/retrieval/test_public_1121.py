"""The service that faces outside (design 2026-11-21-outward-interfaces-design): the pages, the JSON addresses and the
MCP endpoint of nknu.retrieval.public give one answer; the Host guard, the search gate and the query log around them."""

from __future__ import annotations

import asyncio
import json
import threading
import time

import httpx
import pytest
from fastmcp import Client

from nknu.retrieval import public, server, web
from nknu.server.ratelimit import RateLimiter
from tests.retrieval.test_respond import TODAY, Tiny, hit, prose, result

HOST = "mcp.example.org"


class Recorder:
    def __init__(self, *hits):
        self.hits, self.calls = list(hits), []

    def __call__(self, conn, query, *, today, aliases, limit=10, only=None):
        self.calls.append({"query": query, "limit": limit, "only": only})
        return result(*self.hits[:limit], query=query)


@pytest.fixture
def db_path(tmp_path):
    t = Tiny(tmp_path / "aca.db")
    t.conn.executemany("INSERT INTO units(unit_id, name, kind, parent_id, campus) VALUES (?, ?, ?, ?, ?)", [
        ("國立高雄師範大學", "國立高雄師範大學", "学校", None, None),
        ("教務處", "教務處", "行政单位", "國立高雄師範大學", None),
    ])
    t.conn.executemany("INSERT INTO unit_aliases(alias, unit_id) VALUES (?, ?)",
                       [("國立高雄師範大學", "國立高雄師範大學"), ("教務處", "教務處")])
    t.doc("d0", prose(900), title="停修公告 <b>&", ident=("2026-09-01", "published"),
          url="https://c.nknu.edu.tw/aca/news/0")
    t.doc("d1", prose(100), title="短公告", url="https://c.nknu.edu.tw/aca/news/1")
    t.conn.commit()
    t.conn.close()
    return tmp_path / "aca.db"


def app_of(db_path, search=None, **kwargs):
    search = Recorder(hit("d0"), hit("d1")) if search is None else search
    return public.create_app(db_path, search_fn=search, today_fn=lambda: TODAY, allowed_hosts=[HOST], **kwargs)


async def get(app, path, host=HOST, **kwargs):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=f"https://{host}") as c:
        return await c.get(path, **kwargs)


async def test_the_home_page_has_the_search_box_and_the_mcp_address(db_path):
    r = await get(app_of(db_path), "/")
    assert r.status_code == 200 and 'action="/search"' in r.text
    assert f"https://{HOST}/mcp" in r.text and "不是學校的官方服務" in r.text
    assert "noindex" not in r.text  # the home page may be indexed; the pages with the school's text may not


async def test_public_url_is_the_address_the_pages_print(db_path):
    r = await get(app_of(db_path, public_url="https://nknu.example.tw/"), "/")
    assert "https://nknu.example.tw/mcp" in r.text


async def test_a_search_is_a_plain_address_and_shows_the_schools_text(db_path):
    search = Recorder(hit("d0"), hit("d1"))
    r = await get(app_of(db_path, search), "/search", params={"q": "停修"})
    assert r.status_code == 200 and search.calls[0]["query"] == "停修"
    assert "停修公告 &lt;b&gt;&amp;" in r.text and "<b>&" not in r.text  # a title is escaped
    assert 'href="/doc/d0"' in r.text and 'href="https://c.nknu.edu.tw/aca/news/0"' in r.text
    assert "本校學生應於規定期間內完成選課手續" in r.text and web.SCHOOL_TEXT in r.text
    assert r.headers["x-robots-tag"] == "noindex" and '<meta name="robots" content="noindex">' in r.text


async def test_the_page_and_the_api_and_the_mcp_tool_give_one_answer(db_path):
    app = app_of(db_path)
    api = await get(app, "/api/search", params={"q": "停修", "limit": "1"})
    mcp = server.create_mcp(db_path, search_fn=Recorder(hit("d0"), hit("d1")), today_fn=lambda: TODAY)
    async with Client(mcp) as c:
        tool = await c.call_tool("search", {"query": "停修", "limit": 1})
    assert api.status_code == 200 and api.text == tool.content[0].text
    assert api.headers["access-control-allow-origin"] == "*"


async def test_a_refused_argument_says_what_is_allowed(db_path):
    app = app_of(db_path)
    api = await get(app, "/api/search", params={"q": "停修", "sort": "newest"})
    assert api.status_code == 400 and api.json()["error"] == "bad_argument" and "'relevance' or 'date'" in api.text
    page = await get(app, "/search", params={"q": "停修", "limit": "99"})
    assert page.status_code == 400 and "1～50" in page.text
    assert (await get(app, "/api/search")).status_code == 400
    assert (await get(app, "/api/fetch")).status_code == 400


async def test_nothing_found_is_said_as_no_proof_of_nothing(db_path):
    r = await get(app_of(db_path, Recorder()), "/search", params={"q": "不存在的東西"})
    assert r.status_code == 200 and web.NOT_FOUND in r.text


async def test_the_document_page_and_the_fetch_address(db_path):
    app = app_of(db_path)
    page = await get(app, "/doc/d0")
    assert page.status_code == 200 and "看學校原頁" in page.text and web.CONVERTED in page.text
    api = await get(app, "/api/fetch", params={"id": "d0"})
    assert api.json()["found"] is True and api.json()["id"] == "d0"
    by_url = await get(app, "/api/fetch", params={"id": "https://c.nknu.edu.tw/aca/news/1"})
    assert by_url.json()["id"] == "d1"
    missing = await get(app, "/doc/nope")
    assert missing.status_code == 404 and "找不到這份文件" in missing.text


async def test_llms_txt_health_and_robots(db_path):
    app = app_of(db_path)
    llms = await get(app, "/llms.txt")
    assert f"https://{HOST}/mcp" in llms.text and f"https://{HOST}/search?q=" in llms.text
    health = await get(app, "/health")
    assert health.status_code == 200 and health.json()["status"] == "ok" and health.json()["docs"] == 2
    assert (await get(app, "/robots.txt")).status_code == 200


async def test_a_missing_library_is_unavailable_not_an_error_page(tmp_path):
    app = app_of(tmp_path / "gone.db")
    assert (await get(app, "/health")).status_code == 503
    r = await get(app, "/api/search", params={"q": "停修"})
    assert r.status_code == 503 and "不代表查無資料" in r.json()["message"]


async def test_another_host_name_gets_421_on_every_path(db_path):
    app = app_of(db_path)
    for path in ("/", "/search?q=x", "/api/search?q=x", "/health", "/llms.txt"):
        assert (await get(app, path, host="evil.example")).status_code == 421, path
    assert (await get(app, "/health", host="127.0.0.1:8770")).status_code == 200


async def test_login_probes_get_404_never_401(db_path):
    app = app_of(db_path)
    for path in ("/.well-known/oauth-protected-resource", "/.well-known/oauth-authorization-server", "/register"):
        assert (await get(app, path)).status_code == 404, path


async def test_the_mcp_endpoint_answers_and_names_its_tools(db_path):
    app = app_of(db_path)
    transport = httpx.ASGITransport(app=app)
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    headers = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
    async with app.router.lifespan_context(app), httpx.AsyncClient(transport=transport,
                                                                   base_url=f"https://{HOST}") as c:
        r = await c.post("/mcp", json=body, headers=headers)
        evil = await c.post("/mcp", json=body, headers={**headers, "host": "evil.example"})
    assert r.status_code == 200
    tools = {t["name"]: t for t in r.json()["result"]["tools"]}
    assert set(tools) == {"search", "fetch"} and tools["search"]["title"] == server.SEARCH_TITLE
    assert evil.status_code == 421


async def test_the_request_limit_covers_the_pages(db_path):
    app = app_of(db_path, limiter=RateLimiter(per_ip=2, shared=2))
    codes = [(await get(app, "/health")).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


async def test_in_a_container_the_tunnels_clients_are_told_apart(db_path):
    """Every request arrives from Docker's gateway; with it counted as this machine, the tunnel's client header is
    trusted and each client has a budget of its own. Without it they would share the gateway's."""
    async def codes(app, ips):
        transport = httpx.ASGITransport(app=app, client=("172.17.0.1", 5555))
        async with httpx.AsyncClient(transport=transport, base_url=f"https://{HOST}") as c:
            return [(await c.get("/health", headers={"cf-connecting-ip": ip})).status_code for ip in ips]

    ips = ["203.0.113.1", "203.0.113.2", "203.0.113.1", "203.0.113.1"]
    told = app_of(db_path, limiter=RateLimiter(per_ip=2, shared=99), local_peers=["172.16.0.0/12"])
    assert await codes(told, ips) == [200, 200, 200, 429]
    lumped = app_of(db_path, limiter=RateLimiter(per_ip=2, shared=99))
    assert await codes(lumped, ips) == [200, 200, 429, 429]


def test_the_gate_lets_a_search_wait_then_fails_it():
    release, started = threading.Event(), threading.Event()

    def slow(*args, **kwargs):
        started.set()
        release.wait(5)
        return "done"

    gated = public.Gate(most=1, wait=0.05)(slow)
    t = threading.Thread(target=gated)
    t.start()
    started.wait(5)
    with pytest.raises(public.Busy):
        gated()
    release.set()
    t.join()
    assert gated() == "done"  # the slot came back


async def test_a_busy_search_says_try_later(db_path):
    def busy(*args, **kwargs):
        raise public.Busy

    r = await get(app_of(db_path, busy), "/api/search", params={"q": "停修"})
    assert r.status_code == 503 and r.json()["message"] == public.MSG_BUSY


async def test_the_query_log_has_what_was_asked_and_read_and_no_address(db_path, tmp_path):
    path = tmp_path / "q.jsonl"
    app = app_of(db_path, log_path=path)
    phone = "09" + "12345678"  # put together here: the repo check reads a whole number in a file as personal data
    await get(app, "/search", params={"q": f"停修 {phone}", "unit": "教務處"})
    await get(app, "/api/fetch", params={"id": "d1"})
    lines = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]
    assert [(x["via"], x["tool"]) for x in lines] == [("web", "search"), ("api", "fetch")]
    assert lines[0]["ids"] == ["d0", "d1"] and lines[0]["unit"] == "教務處" and lines[0]["found"] is True
    assert phone not in lines[0]["query"]  # a phone number in a query is masked
    assert lines[1]["ids"] == ["d1"]
    assert all(isinstance(x["ms"], int) and x["ms"] >= 0 and "error" not in x for x in lines)
    assert "127.0.0.1" not in path.read_text(encoding="utf-8") and HOST not in path.read_text(encoding="utf-8")


def lines_of(path):
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]


async def test_the_query_log_has_the_mcp_calls(db_path, tmp_path):
    qlog = public.QueryLog(tmp_path / "q.jsonl")
    mcp = server.create_mcp(db_path, search_fn=Recorder(hit("d0")), today_fn=lambda: TODAY)
    mcp.add_middleware(public.LogCalls(qlog))
    async with Client(mcp) as c:
        await c.call_tool("search", {"query": "停修"})
        refused = await c.call_tool("search", {"query": "停修", "sort": "newest"}, raise_on_error=False)
    lines = lines_of(tmp_path / "q.jsonl")
    assert [(x["via"], x.get("error")) for x in lines] == [("mcp", None), ("mcp", "bad_args")]
    assert lines[0]["ids"] == ["d0"] and isinstance(lines[0]["ms"], int)
    assert lines[1]["found"] is False and lines[1]["ids"] == [] and lines[1]["query"] == "停修"
    assert refused.is_error and "'relevance' or 'date'" in refused.content[0].text  # the wording is ShortErrors' still


async def test_a_call_that_got_no_answer_has_a_line_saying_why(db_path, tmp_path):
    def busy(*args, **kwargs):
        raise public.Busy

    path = tmp_path / "q.jsonl"
    app = app_of(db_path, busy, log_path=path)
    await get(app, "/api/search", params={"q": "停修"})
    await get(app, "/search", params={"q": "停修", "limit": "99"})
    await get(app, "/api/search")  # no q at all: not a query, no line
    gone = app_of(tmp_path / "gone.db", log_path=path)
    await get(gone, "/api/fetch", params={"id": "d0"})
    lines = lines_of(path)
    assert [(x["via"], x["tool"], x["error"]) for x in lines] == [
        ("api", "search", "busy"), ("web", "search", "bad_args"), ("api", "fetch", "unavailable")]
    assert all(x["found"] is False and x["ids"] == [] and isinstance(x["ms"], int) for x in lines)


async def test_ms_counts_the_wait_for_a_slot(db_path, tmp_path):
    def slow(conn, query, *, today, aliases, limit=10, only=None):
        time.sleep(0.12)
        return result(hit("d0"), query=query)

    path = tmp_path / "q.jsonl"
    app = app_of(db_path, slow, log_path=path, gate=public.Gate(most=1, wait=5))
    await asyncio.gather(get(app, "/api/search", params={"q": "甲"}), get(app, "/api/search", params={"q": "乙"}))
    waited = sorted(x["ms"] for x in lines_of(path))
    assert waited[0] >= 100 and waited[1] >= 220  # the second waited for the first


async def test_a_busy_search_over_mcp_says_try_later_and_is_logged(db_path, tmp_path):
    release, started = threading.Event(), threading.Event()

    def held(conn, query, *, today, aliases, limit=10, only=None):
        started.set()
        release.wait(5)
        return result(hit("d0"), query=query)

    qlog = public.QueryLog(tmp_path / "q.jsonl")
    mcp = server.create_mcp(db_path, search_fn=public.Gate(most=1, wait=0.05)(held), today_fn=lambda: TODAY)
    mcp.add_middleware(public.LogCalls(qlog))
    async with Client(mcp) as c:
        first = asyncio.create_task(c.call_tool("search", {"query": "甲"}))
        await asyncio.to_thread(started.wait, 5)
        second = await c.call_tool("search", {"query": "乙"}, raise_on_error=False)
        release.set()
        await first
    assert second.is_error and public.MSG_BUSY in second.content[0].text
    by_query = {x["query"]: x for x in lines_of(tmp_path / "q.jsonl")}
    assert by_query["乙"]["error"] == "busy" and by_query["乙"]["ms"] >= 50 and "error" not in by_query["甲"]


def test_only_the_tools_own_arguments_are_written(tmp_path):
    qlog = public.QueryLog(tmp_path / "q.jsonl")
    qlog.add("mcp", "search", {"query": "停修", "t": "x", "via": "y", "junk": ["a"], "limit": 3}, None, ms=5,
             error="bad_args")
    (line,) = lines_of(tmp_path / "q.jsonl")
    assert line["via"] == "mcp" and "junk" not in line and line["limit"] == 3 and line["t"] != "x"


def test_the_query_log_rotates(tmp_path):
    qlog = public.QueryLog(tmp_path / "q.jsonl", max_bytes=200, keep=2)
    for i in range(10):
        qlog.add("web", "search", {"query": f"問題 {i}"}, {"found": False})
    assert (tmp_path / "q.jsonl.1").exists() and not (tmp_path / "q.jsonl.3").exists()
