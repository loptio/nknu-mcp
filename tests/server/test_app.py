import asyncio
import json
import shutil
import sqlite3
from datetime import date, timedelta

import httpx
import pytest
from fastmcp import Client
from starlette.applications import Starlette
from typer.testing import CliRunner

from nknu.admin.cli import app as cli_app
from nknu.build.servedb import build_serve_db
from nknu.core.timeutil import iso_now, now_utc
from nknu.server import app as app_module
from nknu.server.app import INSTRUCTIONS, create_app, create_mcp
from nknu.server.envelope import ERROR_BUDGET, count_tokens, render_text
from nknu.server.query import get_calendar, get_fact, get_hours
from nknu.server.search import search
from nknu.server.servedb_reader import ServeDB
from nknu.settings import REPO_ROOT, load_settings
from tests.server.conftest import TODAY, TPL

H = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
LIST = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
TOOLS = ["nknu_get_calendar", "nknu_get_fact", "nknu_get_hours", "nknu_search"]


@pytest.fixture
def data_dir(world, tmp_path):
    """A private copy of the fixture world's serve.db: tests may log misses next to it or replace it."""
    shutil.copy2(world[1], tmp_path / "serve.db")
    return tmp_path


@pytest.fixture
def db(data_dir):
    return ServeDB(data_dir / "serve.db", templates_path=TPL)


@pytest.fixture
def settings(data_dir):
    return load_settings({"NKNU_DATA_DIR": str(data_dir), "NKNU_CONFIG_DIR": str(REPO_ROOT / "config")})


@pytest.fixture
async def client(db):
    async with Client(create_mcp(db)) as c:
        yield c


async def _serve(settings):
    """The app's lifespan is entered and left by one holder task: pytest-asyncio runs a fixture's setup and teardown
    in different tasks, and the session manager's anyio task group must be exited by the task that entered it."""
    app = create_app(settings)
    started, stop = asyncio.Event(), asyncio.Event()

    async def hold():
        async with app.router.lifespan_context(app):
            started.set()
            await stop.wait()

    holder = asyncio.create_task(hold())
    await started.wait()
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8765") as c:
            yield c
    finally:
        stop.set()
        await holder


@pytest.fixture
async def http(settings):
    async for c in _serve(settings):
        yield c


@pytest.fixture
async def http_old_build(settings, data_dir):
    conn = sqlite3.connect(data_dir / "serve.db")
    conn.execute("UPDATE meta SET value=? WHERE key='built_at'", (iso_now(now_utc() - timedelta(hours=37)),))
    conn.commit()
    conn.close()
    async for c in _serve(settings):
        yield c


def _no_clock(env: dict) -> dict:
    """An envelope without its today/time stamp (the tool and the direct call run a moment apart)."""
    return {k: v for k, v in env.items() if k != "today"}


# ---------------------------------------------------------------- tools (in process)

async def test_four_tools_no_output_schema_under_budget(client):
    tools = await client.list_tools()
    assert sorted(t.name for t in tools) == TOOLS
    # mcp 2.3 models use snake_case attributes (outputSchema/readOnlyHint are the wire aliases)
    assert all(t.output_schema is None and t.annotations.read_only_hint for t in tools)
    assert all(t.annotations.open_world_hint is False for t in tools)
    assert count_tokens([t.model_dump(exclude_none=True, by_alias=True, mode="json") for t in tools]) <= 1000


async def test_tool_parameters_are_flat(client):
    for t in await client.list_tools():
        props = t.input_schema["properties"]
        assert all(p.get("type") in ("string", "integer", "boolean") or "anyOf" in p for p in props.values()), t.name
    hours = next(t for t in await client.list_tools() if t.name == "nknu_get_hours")
    assert hours.input_schema["required"] == ["place"]
    cal = next(t for t in await client.list_tools() if t.name == "nknu_get_calendar")
    assert {"from_date", "to_date", "keyword", "limit"} == set(cal.input_schema["properties"])


def test_instructions_first_paragraph():
    p = INSTRUCTIONS.split("\n\n")[0]
    assert len(p) <= 500 and all(w in p for w in ("高師大", "和平", "燕巢", "current"))
    assert all(w in p for w in ("caveat", "公告", "有效期", "來源"))
    second = INSTRUCTIONS.split("\n\n")[1]
    assert all(t in second for t in TOOLS)


async def test_instructions_reach_the_client(client):
    assert client.instructions == INSTRUCTIONS


async def test_call_returns_structured_and_text(client):
    r = await client.call_tool("nknu_get_hours", {"place": "重訓室", "date": "2026-10-22"})
    assert r.structured_content["status"] and r.content[0].text
    assert r.content[0].text == render_text(r.structured_content) and not r.is_error


async def test_tools_answer_as_the_query_functions(client, db):
    e = await client.call_tool("nknu_get_hours", {"place": "和平重訓室", "date": "2026-10-22", "days": 3})
    assert _no_clock(e.structured_content) == _no_clock(get_hours(db, "和平重訓室", "2026-10-22", days=3))
    c = await client.call_tool("nknu_get_calendar", {"from_date": "2026-11-01", "to_date": "2026-11-30", "keyword": "期中"})
    assert _no_clock(c.structured_content) == _no_clock(get_calendar(db, "2026-11-01", "2026-11-30", "期中"))
    assert c.structured_content["found"] is True
    s = await client.call_tool("nknu_search", {"query": "圖書館 期中考 延長開館", "on_date": "2026-10-24", "limit": 3})
    assert _no_clock(s.structured_content) == _no_clock(search(db, "圖書館 期中考 延長開館", on_date="2026-10-24", limit=3))
    fid = e.structured_content["facts"][0]["id"]
    f = await client.call_tool("nknu_get_fact", {"id": fid, "include_history": True})
    assert _no_clock(f.structured_content) == _no_clock(get_fact(db, fid, include_history=True))
    assert f.structured_content["found"] is True and f.structured_content["history"]


async def test_bad_args_short_error(client):
    r = await client.call_tool("nknu_get_hours", {"place": "重訓室", "days": 99}, raise_on_error=False)
    assert r.is_error and count_tokens(r.content[0].text) <= 80
    assert "days" in r.content[0].text


LONG_CJK = "二〇二六年十月二十二日" * 40
BAD_CALLS = [
    ("nknu_get_hours", {"place": "和平重訓室", "date": LONG_CJK}),
    ("nknu_get_hours", {"place": "和平重訓室", "date": "㐀㐁㐂㐃" * 100}),  # rare characters: 2–3 tokens each
    ("nknu_get_hours", {"place": "和平重訓室", "date": "𠀀😀" * 100}),
    ("nknu_get_hours", {"place": "和平重訓室", "date": "\x00\n\u202e" * 100}),
    ("nknu_get_hours", {"place": "和平重訓室", "days": "很多天" * 40}),
    ("nknu_get_hours", {"place": "和平重訓室", "days": 2.5}),
    ("nknu_get_hours", {"place": "和平重訓室", "date": "9999-12-31", "days": 14}),
    ("nknu_get_hours", {"place": "和平重訓室", "date": "9999-12-31"}),
    ("nknu_get_hours", {"place": LONG_CJK}),
    ("nknu_get_hours", {}),
    ("nknu_get_hours", {"place": "和平重訓室", "㐀" * 100: 1}),
    ("nknu_search", {"query": "期中考", "on_date": "㐀" * 300}),
    ("nknu_search", {"query": "期中考", "on_date": "9999-12-31"}),
    ("nknu_search", {"query": LONG_CJK}),
    ("nknu_search", {"query": "   "}),
    ("nknu_search", {"query": "期中考", "kind": "全部" * 50}),
    ("nknu_search", {"query": "期中考", "campus": "第三校區" * 30}),
    ("nknu_search", {"query": "期中考", "limit": 0}),
    ("nknu_get_calendar", {"from_date": "9999-12-31"}),
    ("nknu_get_calendar", {"from_date": "😀" * 100}),
    ("nknu_get_calendar", {"from_date": "2026-11-01", "to_date": "2026-10-01"}),
    ("nknu_get_calendar", {"keyword": LONG_CJK}),
    ("nknu_get_fact", {"id": "x" * 5000}),
    ("nknu_get_fact", {"id": "x", "include_history": "也許" * 30}),
]


@pytest.mark.parametrize(("tool", "args"), BAD_CALLS)
async def test_every_error_is_short(client, tool, args):
    r = await client.call_tool(tool, args, raise_on_error=False)
    text = r.content[0].text
    assert r.is_error and text.startswith("錯誤 ")
    assert count_tokens(text) <= ERROR_BUDGET, text
    assert r.structured_content is None or count_tokens(r.structured_content) <= ERROR_BUDGET
    assert "Traceback" not in text and "pydantic" not in text and "http" not in text
    long_inputs = [s for s in [*args, *args.values()] if isinstance(s, str) and len(s) > 30]
    assert not any(s[:30] in text for s in long_inputs)  # a long argument is echoed cut, never in full


async def test_missed_query_logged_masked(client, data_dir):
    await client.call_tool("nknu_search", {"query": "0912-345-678 是誰的電話"})
    line = (data_dir / "misses.jsonl").read_text()
    assert "[MASKED]" in line and "0912" not in line
    rec = json.loads(line)
    assert set(rec) == {"date", "tool", "query"} and rec["tool"] == "nknu_search" and rec["query"] == "[MASKED] 是誰的電話"


async def test_missed_place_logged_and_found_not_logged(client, data_dir):
    await client.call_tool("nknu_search", {"query": "和平重訓室 開放時間", "on_date": "2026-10-22"})
    await client.call_tool("nknu_get_hours", {"place": "和平重訓室", "date": "2026-10-22"})
    assert not (data_dir / "misses.jsonl").exists()
    await client.call_tool("nknu_get_hours", {"place": "不存在的館"})
    await client.call_tool("nknu_get_calendar", {"keyword": "不存在的活動"})  # calendar misses are not logged (§7.8)
    lines = (data_dir / "misses.jsonl").read_text().splitlines()
    assert [json.loads(x)["tool"] for x in lines] == ["nknu_get_hours"]
    rec = json.loads(lines[0])
    assert rec["query"] == "不存在的館" and date.fromisoformat(rec["date"])


async def test_unavailable_serve_db_is_a_short_error(client, data_dir):
    (data_dir / "serve.db").unlink()
    r = await client.call_tool("nknu_get_hours", {"place": "和平重訓室"}, raise_on_error=False)
    assert r.is_error and "unavailable" in r.content[0].text and count_tokens(r.content[0].text) <= ERROR_BUDGET


async def test_unexpected_failure_is_a_short_error(client, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("secret internals /etc/passwd")
    monkeypatch.setattr(app_module, "get_hours", boom)
    r = await client.call_tool("nknu_get_hours", {"place": "和平重訓室"}, raise_on_error=False)
    assert r.is_error and "internal" in r.content[0].text and "secret" not in r.content[0].text


# ---------------------------------------------------------------- HTTP

async def test_host_protection(http):
    assert (await http.post("/mcp", json=LIST, headers={**H, "host": "evil.example"})).status_code == 421


async def test_origin_protection(http):
    assert (await http.post("/mcp", json=LIST, headers={**H, "origin": "https://evil.example"})).status_code == 403


async def test_allowed_host_and_origin(data_dir):
    s = load_settings({"NKNU_DATA_DIR": str(data_dir), "NKNU_ALLOWED_HOSTS": "mcp.example.org",
                       "NKNU_ALLOWED_ORIGINS": "https://claude.ai"})
    async for http in _serve(s):
        r = await http.post("/mcp", json=LIST, headers={**H, "host": "mcp.example.org", "origin": "https://claude.ai"})
        assert r.status_code == 200
        assert (await http.post("/mcp", json=LIST, headers={**H, "host": "other.example.org"})).status_code == 421


async def test_get_mcp_405(http):
    assert (await http.get("/mcp")).status_code == 405


async def test_both_protocol_versions(http, client):
    # the older protocol 2025-11-25 answers tools/list with 200; fastmcp.Client negotiates 2026-07-28 by default
    r = await http.post("/mcp", json=LIST, headers={**H, "mcp-protocol-version": "2025-11-25"})
    assert r.status_code == 200 and sorted(t["name"] for t in r.json()["result"]["tools"]) == TOOLS
    assert await client.list_tools() and client.protocol_version == "2026-07-28"


async def test_tools_call_over_http(http):
    call = {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "nknu_get_hours", "arguments": {"place": "和平重訓室", "date": "2026-10-22"}}}
    r = await http.post("/mcp", json=call, headers={**H, "mcp-protocol-version": "2025-11-25"})
    res = r.json()["result"]
    assert r.status_code == 200 and res["structuredContent"]["status"] == "current" and not res.get("isError")
    assert res["content"][0]["text"] == render_text(res["structuredContent"])


async def test_health_ok(http, db):
    h = (await http.get("/health")).json()
    assert h["status"] == "ok"
    meta = db.meta()
    assert h == {"status": "ok", "build_id": meta["build_id"], "built_at": meta["built_at"],
                 "facts_version": int(meta["facts_version"])}


async def test_health_stale_503(http_old_build):
    r = await http_old_build.get("/health")
    assert r.status_code == 503 and r.json()["status"] == "stale"


async def test_health_unreadable_503(http, data_dir):
    (data_dir / "serve.db").unlink()
    assert (await http.get("/health")).status_code == 503
    (data_dir / "serve.db").write_bytes(b"not a database at all" * 100)
    assert (await http.get("/health")).status_code == 503


async def test_rate_limit_applies_to_the_app(http):
    codes = [(await http.get("/health")).status_code for _ in range(61)]
    assert codes[:60] == [200] * 60 and codes[60] == 429


def test_refuses_start_without_serve_db(tmp_path):
    with pytest.raises(SystemExit):
        create_app(load_settings({"NKNU_DATA_DIR": str(tmp_path)}))


def test_refuses_start_without_build_id(tmp_path):
    conn = sqlite3.connect(tmp_path / "serve.db")
    conn.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.commit()
    conn.close()
    with pytest.raises(SystemExit):
        create_app(load_settings({"NKNU_DATA_DIR": str(tmp_path)}))


def test_refuses_start_when_startup_checks_fail(settings, monkeypatch):
    monkeypatch.setattr(app_module, "check_startup", lambda s: ["startup: tzdata missing"])
    with pytest.raises(SystemExit, match="tzdata"):
        create_app(settings)


async def test_serve_db_swap_picked_up(http, world, data_dir):
    old = (await http.get("/health")).json()["build_id"]
    src, dst = sqlite3.connect(world[0]), sqlite3.connect(data_dir / "facts.db")
    src.backup(dst)
    src.close()
    dst.close()
    new_id = build_serve_db(data_dir / "facts.db", data_dir / "serve.db", today=TODAY, templates_path=TPL).build_id
    assert new_id != old and (await http.get("/health")).json()["build_id"] == new_id


def test_cli_serve_runs_uvicorn(settings, data_dir, monkeypatch):
    seen = {}
    monkeypatch.setattr("uvicorn.run", lambda app, **kw: seen.update(app=app, **kw))
    r = CliRunner().invoke(cli_app, ["serve", "--port", "8799"], env={"NKNU_DATA_DIR": str(data_dir)})
    assert r.exit_code == 0, r.output
    assert isinstance(seen["app"], Starlette) and seen["host"] == "127.0.0.1" and seen["port"] == 8799
    assert seen["proxy_headers"] is False  # the peer address must stay the TCP peer (see ratelimit)
