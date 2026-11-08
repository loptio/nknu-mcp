"""Task D: the two MCP tools (in memory through fastmcp.Client, with a fake search_fn) and the two command lines. Since
plan 2026-11-02 T8 the default search is nknu.retrieval.hybrid.hybrid_search."""

from __future__ import annotations

import json
import sqlite3
import sys
import types

import pytest
from fastmcp import Client, FastMCP

from nknu.retrieval import cli, server
from nknu.retrieval.hybrid import HybridConfig
from nknu.retrieval.respond import build_response, read_response
from nknu.settings import REPO_ROOT
from tests.retrieval.test_respond import ALIASES, TODAY, Tiny, hit, prose, result

# plan 2026-11-02 T9 (deliberate): texts that hold for a skeleton and a pipeline build alike (no 「骨架版」, no format
# list), and the spec's 「换几个说法再查」 (spec §3.5, the owner's 11-01 idea). Owner 11-03 (deliberate, addendum L): no
# series, so SEARCH_DESCRIPTION lost 「；同系列較舊的公告收成一行」 and READ_DESCRIPTION 「；id 是 s 開頭的系列編號時列出
# 同系列全部版本」 (each clause with the 「；」 before it, the 「。」 after it kept). Plan 2026-11-08 T4 (deliberate): one
# database of many sites, which clients keep the tool texts of while it is replaced: no unit, no site address, no
# count or year; what a build holds is in each answer (coverage, data_as_of)
SEARCH_DESCRIPTION = ("搜尋已收錄的高師大網站的公開網頁、公告、附件與行事曆，回傳學校原文、日期與出處。"
                      "前 3 筆給全文（長文件給命中的章節和目錄），其餘給原文摘要。"
                      "query 請用繁體中文和台灣用語的關鍵字或問句"
                      "（例如用「軟體」不用「軟件」、用「資訊」不用「信息」）。"
                      "查不到或結果不對題時，請換幾個說法再查（同義詞、全稱或簡稱、學校公告的寫法），"
                      "不要只查一次就下結論。"
                      "收錄了哪些網站、資料截至何時，見每次回傳的 coverage 和 data_as_of。")
READ_DESCRIPTION = ("用 search 回傳的 id 讀一份文件：part 留空讀全文（太長給目錄和開頭），part=節號讀某一節，"
                    "part=toc 只看目錄。")
# what an agent is told at the MCP handshake (plan 2026-11-02 T9, deliberate; was the main session's 11-01 wording;
# plan 2026-11-08 T4, deliberate: no unit, no site address, no year)
INSTRUCTIONS = ("高師大校園資訊：搜尋已收錄的高師大網站的公開網頁、公告、附件與行事曆；收錄範圍和資料日期見每次回傳的 "
                "coverage 與 data_as_of。回傳都是學校原文加上日期和出處，不含模型寫的句子。查詢請用繁體中文和台灣用語；"
                "結果都不能回答問題時，先換幾個說法再查；仍然查不到，請照實告訴使用者查無資料。")
# spec §3.5 (borrowed from WeKnora): a failure must not read as 查無資料
MSG_UNAVAILABLE = "資料暫時無法讀取，請稍後再試。這次失敗不代表查無資料。"
MSG_INTERNAL = "查詢失敗，請稍後再試。這次失敗不代表查無資料。"


class FakeSearch:
    """Stands in for nknu.retrieval.search.search_docs: records its calls and answers with fixed hits."""

    def __init__(self, *hits, effect=None):
        self.hits, self.effect, self.calls = list(hits), effect, []

    def __call__(self, conn, query, *, today, aliases, limit=10):
        self.calls.append((query, today, aliases))
        if self.effect is not None:
            self.effect(conn)
        return result(*self.hits, query=query)


@pytest.fixture
def db_path(tmp_path):
    t = Tiny(tmp_path / "aca.db")
    t.doc("dp1", prose(500), title="115學年度第1學期線上申請停修公告", ident=("2026-10-21", "published"))
    t.date("dp1", "published", "2026-10-21", "2026/10/21 上午 10:00:00")
    t.conn.commit()
    t.conn.close()
    return tmp_path / "aca.db"


def mcp_for(db_path, search_fn):
    return server.create_mcp(db_path, search_fn=search_fn, today_fn=lambda: TODAY)


async def call(db_path, search_fn, tool, args):
    async with Client(mcp_for(db_path, search_fn)) as c:
        return await c.call_tool(tool, args, raise_on_error=False)


def fake_search_module(monkeypatch, fn) -> None:
    """T8's module as the lazy imports in server.default_search and server.config_from see it (deliberate change, T8:
    the default search is hybrid_search now); HybridConfig stays the real one."""
    module = types.ModuleType("nknu.retrieval.hybrid")
    module.hybrid_search = fn
    module.HybridConfig = HybridConfig
    monkeypatch.setitem(sys.modules, "nknu.retrieval.hybrid", module)


def config_recorder(seen: list):
    """A hybrid_search stand-in that records the config it was called with."""
    def search(conn, query, *, today, aliases, limit=10, config=None):
        seen.append(config)
        return result(hit("dp1", [1]), query=query)
    return search


# ---------------------------------------------------------------- tools

async def test_server_tools_listed(db_path):
    async with Client(mcp_for(db_path, FakeSearch())) as c:
        tools = {t.name: t for t in await c.list_tools()}
    assert sorted(tools) == ["read", "search"]
    assert all(t.annotations.read_only_hint and t.annotations.open_world_hint is False for t in tools.values())
    assert all(t.output_schema is None for t in tools.values())
    assert tools["search"].description == SEARCH_DESCRIPTION
    assert tools["read"].description == READ_DESCRIPTION
    assert set(tools["search"].input_schema["properties"]) == {"query"}
    assert tools["search"].input_schema["required"] == ["query"]
    assert set(tools["read"].input_schema["properties"]) == {"id", "part"}
    assert tools["read"].input_schema["required"] == ["id"]


async def test_server_instructions_at_handshake(db_path):
    # an agent learns the coverage, the source-only answers and the Traditional Chinese query rule before any call
    async with Client(mcp_for(db_path, FakeSearch())) as c:
        assert c.instructions == INSTRUCTIONS


async def test_server_search_roundtrip(db_path):
    fake = FakeSearch(hit("dp1", [1]))
    r = await call(db_path, fake, "search", {"query": "115-1 停修"})
    assert not r.is_error and r.structured_content is None
    assert len(r.content) == 1 and r.content[0].type == "text"
    body = json.loads(r.content[0].text)
    assert r.content[0].text == json.dumps(body, ensure_ascii=False, separators=(",", ":"))  # compact, no \u escapes
    conn = sqlite3.connect(db_path)
    try:
        assert body == build_response(conn, result(hit("dp1", [1]), query="115-1 停修"), today=TODAY)
    finally:
        conn.close()
    assert body["found"] is True and body["results"][0]["id"] == "dp1"
    assert fake.calls == [("115-1 停修", TODAY, ALIASES)]  # aliases come from meta.aliases_json


async def test_server_read_roundtrip(db_path):
    r = await call(db_path, FakeSearch(), "read", {"id": "dp1"})
    conn = sqlite3.connect(db_path)
    try:
        assert json.loads(r.content[0].text) == read_response(conn, "dp1", "", today=TODAY)
    finally:
        conn.close()
    numbered = await call(db_path, FakeSearch(), "read", {"id": "dp1", "part": 0})  # a number for part is fine
    assert not numbered.is_error and json.loads(numbered.content[0].text)["content"] == prose(500)
    missing = json.loads((await call(db_path, FakeSearch(), "read", {"id": "dNOPE"})).content[0].text)
    assert missing["found"] is False and missing["notes"] == ["【系統】查無此 id"]


@pytest.mark.parametrize("query", ["", "!!!", 'AND OR NEAR * ^ "'])
async def test_server_odd_query_is_not_an_error(db_path, query):
    r = await call(db_path, FakeSearch(), "search", {"query": query})
    assert not r.is_error and json.loads(r.content[0].text)["found"] is False


@pytest.mark.parametrize(("tool", "args", "word"), [
    ("search", {"query": "停" * 301}, "query"),
    ("read", {"id": "d" * 101}, "id"),
    ("read", {"id": "dp1", "part": "1" * 21}, "part"),
])
async def test_server_argument_limits(db_path, tool, args, word):
    fake = FakeSearch()
    r = await call(db_path, fake, tool, args)
    text = r.content[0].text
    assert r.is_error and text.startswith("錯誤 bad_argument：") and word in text and len(text) < 60
    assert fake.calls == []


@pytest.mark.parametrize(("tool", "args"), [("search", {}), ("read", {"part": "1"}), ("search", {"query": 5}),
                                            ("search", {"query": "停修", "kind": "any"})])
async def test_server_bad_arguments_short_error(db_path, tool, args):
    r = await call(db_path, FakeSearch(), tool, args)
    text = r.content[0].text
    assert r.is_error and text.startswith("錯誤 bad_argument：") and len(text) < 80
    assert "Traceback" not in text and "pydantic" not in text and "http" not in text


async def test_server_unavailable_database(db_path):
    db_path.unlink()
    for tool, args in (("search", {"query": "停修"}), ("read", {"id": "dp1"})):
        r = await call(db_path, FakeSearch(), tool, args)
        assert r.is_error and r.content[0].text == f"錯誤 unavailable：{MSG_UNAVAILABLE}"  # T9 (deliberate)


async def test_server_opens_read_only(db_path):
    writer = FakeSearch(effect=lambda conn: conn.execute("CREATE TABLE x(a)"))
    r = await call(db_path, writer, "search", {"query": "停修"})
    assert r.is_error and r.content[0].text.startswith("錯誤 unavailable")
    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute("SELECT count(*) FROM sqlite_master WHERE name='x'").fetchone()[0] == 0
    finally:
        conn.close()


async def test_server_internal_error_masked(db_path):
    def boom(conn):
        raise RuntimeError("secret internals /etc/passwd")
    r = await call(db_path, FakeSearch(effect=boom), "search", {"query": "停修"})
    assert r.is_error and r.content[0].text == f"錯誤 internal：{MSG_INTERNAL}"  # T9 (deliberate)
    assert "secret" not in r.content[0].text and "passwd" not in r.content[0].text


async def test_errors_say_failure_is_not_no_data(db_path):
    # Review Focus 1: a real failure says 「這次失敗不代表查無資料」, so an agent does not tell the student the school
    # has no such information; a refused argument is the caller's to fix and says nothing of the kind
    def boom(conn):
        raise RuntimeError("inference failed")
    failures = [await call(db_path, FakeSearch(effect=boom), "search", {"query": "停修"})]
    refused = await call(db_path, FakeSearch(), "search", {"query": "停" * 301})
    db_path.unlink()
    failures += [await call(db_path, FakeSearch(), tool, args)
                 for tool, args in (("search", {"query": "停修"}), ("read", {"id": "dp1"}))]
    assert all(r.is_error and r.content[0].text.endswith("這次失敗不代表查無資料。") for r in failures)
    assert refused.is_error and "查無資料" not in refused.content[0].text


def test_tool_texts_hold_for_both_builds():
    # the server never reads the database to pick its texts, so they name no build and no file formats
    assert "骨架" not in server.INSTRUCTIONS and "骨架" not in server.SEARCH_DESCRIPTION
    assert "PDF" not in server.SEARCH_DESCRIPTION and "Office" not in server.SEARCH_DESCRIPTION
    assert "換幾個說法再查" in server.SEARCH_DESCRIPTION and "換幾個說法再查" in server.INSTRUCTIONS


def test_tool_texts_name_no_series():
    # owner 11-03: no series, so no tool text promises folded versions or a series id to read
    for text in (server.SEARCH_DESCRIPTION, server.READ_DESCRIPTION, server.INSTRUCTIONS):
        assert "系列" not in text and "收成一行" not in text and "s 開頭" not in text


def test_tool_texts_name_no_unit_and_no_count():
    # plan 2026-11-08 T4: clients keep the tool texts while the database is replaced by one of more sites, so they
    # name no unit or site and give no number but the program's own (前 3 筆); the server is nknu, no longer nknu-aca
    for text in (server.SEARCH_DESCRIPTION, server.READ_DESCRIPTION, server.INSTRUCTIONS):
        assert not [w for w in ("教務", "c.nknu", "/aca", "只收", "2023") if w in text], text
        assert {ch for ch in text if ch.isdigit()} <= {"3"}, text
    assert server.NAME == "nknu" and server.DEFAULT_DB.name == "aca.db"  # the default database stays where it was


async def test_server_read_of_an_s_id_is_unknown(db_path):
    # the read tool answers a series id of a build before 11-03 as any unknown id
    for sid in ("s1a2b3c4d5", "sNOPE"):
        r = await call(db_path, FakeSearch(), "read", {"id": sid})
        body = json.loads(r.content[0].text)
        assert not r.is_error and body["found"] is False and body["notes"] == ["【系統】查無此 id"], sid


# ---------------------------------------------------------------- python -m nknu.retrieval.server

def test_main_stdio_by_default(db_path, monkeypatch):
    runs = []
    monkeypatch.setattr(FastMCP, "run", lambda self, transport=None, **kw: runs.append((self.name, transport, kw)))
    fake_search_module(monkeypatch, FakeSearch())
    assert server.main(["--db", str(db_path)]) == 0
    assert runs == [("nknu", "stdio", {"show_banner": False})]  # plan 2026-11-08 T4 (deliberate): was nknu-aca


def test_main_http_binds_loopback(db_path, monkeypatch):
    runs = []
    monkeypatch.setattr(FastMCP, "run", lambda self, transport=None, **kw: runs.append((transport, kw)))
    fake_search_module(monkeypatch, FakeSearch())
    assert server.main(["--db", str(db_path), "--http"]) == 0
    transport, kw = runs[0]
    assert transport == "http" and kw["host"] == "127.0.0.1" and kw["port"] == 8766
    assert kw["host_origin_protection"] is True
    server.main(["--db", str(db_path), "--http", "--port", "9001"])
    assert runs[1][1]["port"] == 9001


async def test_default_search_is_hybrid(db_path, monkeypatch):
    # renamed from test_default_search_is_task_a (T8, deliberate): the server's default search is hybrid_search
    fake = FakeSearch(hit("dp1", [1]))
    fake_search_module(monkeypatch, fake)
    async with Client(server.create_mcp(db_path, today_fn=lambda: TODAY)) as c:
        r = await c.call_tool("search", {"query": "停修"})
    assert json.loads(r.content[0].text)["results"][0]["id"] == "dp1" and fake.calls


async def test_create_mcp_passes_the_config(db_path, monkeypatch):
    seen = []
    fake_search_module(monkeypatch, config_recorder(seen))
    off = HybridConfig(dense=False, rerank=False)
    async with Client(server.create_mcp(db_path, today_fn=lambda: TODAY, config=off)) as c:
        await c.call_tool("search", {"query": "停修"})
    async with Client(server.create_mcp(db_path, today_fn=lambda: TODAY)) as c:
        await c.call_tool("search", {"query": "停修"})
    assert seen == [off, None]


def test_main_flags_turn_models_off(db_path, monkeypatch):
    got = []
    real = server.create_mcp
    monkeypatch.setattr(server, "create_mcp", lambda db, **kw: got.append(kw.get("config")) or real(db, **kw))
    monkeypatch.setattr(FastMCP, "run", lambda self, transport=None, **kw: None)
    assert server.main(["--db", str(db_path), "--no-dense", "--no-rerank"]) == 0
    assert server.main(["--db", str(db_path), "--no-rerank"]) == 0
    assert server.main(["--db", str(db_path), "--no-dense"]) == 0
    assert server.main(["--db", str(db_path)]) == 0
    assert got == [HybridConfig(False, False), HybridConfig(dense=True, rerank=False),
                   HybridConfig(dense=False, rerank=True), None]


def test_default_db_is_the_pipeline_build():
    assert server.DEFAULT_DB == REPO_ROOT / "research" / "pipeline_2026-11-02" / "aca.db"


def test_main_missing_db(tmp_path, capsys):
    assert server.main(["--db", str(tmp_path / "none.db")]) == 2
    assert "none.db" in capsys.readouterr().err


# ---------------------------------------------------------------- python -m nknu.retrieval.cli

def test_cli_query(db_path, monkeypatch, capsys):
    fake = FakeSearch(hit("dp1", [1]))
    fake_search_module(monkeypatch, fake)
    assert cli.main(["--db", str(db_path), "115-1 停修"]) == 0
    out, err = capsys.readouterr()
    body = json.loads(out)
    assert body["results"][0]["id"] == "dp1" and fake.calls[0][0] == "115-1 停修" and fake.calls[0][2] == ALIASES
    assert err.strip() == f"{len(out.strip().encode('utf-8'))} bytes"


def test_cli_flags_turn_models_off(db_path, monkeypatch, capsys):
    seen = []
    fake_search_module(monkeypatch, config_recorder(seen))
    assert cli.main(["--db", str(db_path), "--no-dense", "--no-rerank", "停修"]) == 0
    assert cli.main(["--db", str(db_path), "停修"]) == 0
    assert seen == [HybridConfig(False, False), None]
    assert json.loads(capsys.readouterr().out.splitlines()[0])["results"][0]["id"] == "dp1"


def test_cli_read(db_path, capsys):
    assert cli.main(["--db", str(db_path), "--read", "dp1", "--part", "0"]) == 0
    out, err = capsys.readouterr()
    assert json.loads(out)["content"] == prose(500) and err.strip().endswith(" bytes")


def test_cli_needs_query_or_read(db_path, tmp_path, capsys):
    with pytest.raises(SystemExit):
        cli.main(["--db", str(db_path)])
    with pytest.raises(SystemExit):
        cli.main(["--db", str(db_path), "停修", "--read", "dp1"])
    assert cli.main(["--db", str(tmp_path / "none.db"), "--read", "dp1"]) == 2
