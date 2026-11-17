"""試用 (nknu.ops.try_page, nknu.ops.mcp_client; structure design 2026-11-17 section 三.1): the search service as a
student's AI agent sees it. The page follows whatever tools/list says (no tool name or parameter written in it), calls
only read-only tools, one call at a time, from the server, and shows each answer as received and laid out. Every
service here is a made-up one on this machine's loopback; no text in it is the school's."""

from __future__ import annotations

import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar

import pytest
from starlette.datastructures import QueryParams
from starlette.testclient import TestClient

from nknu.ops import try_page, web
from nknu.ops.mcp_client import Exchange, McpClient, approx_tokens
from nknu.ops.try_page import TryPage, arguments, fields, follow_up, form_html
from tests.ops.library import models
from tests.ops.test_theme import SIZES, THEME
from tests.retrieval.test_search import TODAY

RO = {"readOnlyHint": True}
FIND = {"name": "lookup", "title": "Lookup", "description": "找一找測試用的文件。", "annotations": RO,
        "inputSchema": {"type": "object", "required": ["term"], "properties": {
            "term": {"type": "string", "description": "要找的字"},
            "top": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5, "description": "幾筆"},
            "mode": {"type": "string", "enum": ["short", "long"], "default": "short"},
            "fresh": {"type": "boolean", "default": True},
            "piece": {"anyOf": [{"type": "string"}, {"type": "integer"}], "default": ""},
            "tags": {"type": "array", "items": {"type": "string"}},
            "order": {"$ref": "#/$defs/Order"}},
            "$defs": {"Order": {"type": "string", "enum": ["best", "new"]}}}}
OPEN = {"name": "open", "description": "用編號讀一份。", "annotations": RO,
        "inputSchema": {"type": "object", "required": ["ref"], "properties": {"ref": {"type": "string"}}}}
WRITE = {"name": "erase", "description": "會改東西的工具。", "annotations": {"readOnlyHint": False},
         "inputSchema": {"type": "object", "required": ["ref"], "properties": {"ref": {"type": "string"}}}}
HELLO = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
         "serverInfo": {"name": "made-up", "version": "9.9"}, "instructions": "這是編的開場說明。\n第二行。"}
ANSWER = {"today": {"date": "2026-11-17", "roc_date": "115年11月17日", "weekday": "週二", "term": "115-1"},
          "coverage": "編的收錄範圍。", "query": "測試", "understood": {"aliases": ["試驗"]}, "found": True,
          "results": [{"rank": 1, "id": "d1", "ref": "d1", "identity": "測試處｜某組｜公告｜發布 2026-10-01（公告頁日期欄）",
                       "title": "第一則測試公告", "url": "https://school.example/news?id=1",
                       "dates": [{"type": "發布", "date": "2026-10-01", "source": "公告頁日期欄"}],
                       "content_type": "全文", "content": "測試的內文" * 60, "brand_new_key": "照樣列出"},
                      {"rank": 2, "id": "d2", "ref": "d2", "identity": "測試處｜附件（PDF）｜無日期",
                       "title": "第二份附件", "url": "https://school.example/f.pdf", "content_type": "摘要",
                       "content": "短短的摘要"}],
          "notes": ["【系統】編的附註一。", "【系統】編的附註二。"]}


class _Mcp(BaseHTTPRequestHandler):
    """A made-up MCP service: answers initialize, tools/list and tools/call, and keeps what it was asked."""

    tools: ClassVar[list] = [FIND, OPEN]
    answer: ClassVar[object] = ANSWER
    error: ClassVar[bool] = False
    stream: ClassVar[bool] = False  # answer as an event stream
    session: ClassVar[str] = ""  # hand out a session id
    delay: ClassVar[float] = 0.0
    asked: ClassVar[list] = []

    def log_message(self, *_args):
        pass

    def do_POST(self):
        sent = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        cls = type(self)
        cls.asked.append((sent.get("method"), sent.get("params"), self.headers.get("Mcp-Session-Id", ""),
                          self.headers["Host"]))
        assert self.headers["Host"].startswith("127.0.0.1:")
        if "id" not in sent:  # a notification
            self.send_response(202)
            self.end_headers()
            return
        if sent["method"] == "initialize":
            result: object = HELLO
        elif sent["method"] == "tools/list":
            result = {"tools": cls.tools}
        else:
            time.sleep(cls.delay)
            text = cls.answer if isinstance(cls.answer, str) else json.dumps(cls.answer, ensure_ascii=False)
            result = {"content": [{"type": "text", "text": text}], "isError": cls.error}
        body = json.dumps({"jsonrpc": "2.0", "id": sent["id"], "result": result}, ensure_ascii=False)
        if cls.stream:
            data, kind = f"event: message\ndata: {body}\n\n".encode(), "text/event-stream"
        else:
            data, kind = body.encode(), "application/json"
        self.send_response(200)
        self.send_header("Content-Type", kind)
        if cls.session:
            self.send_header("Mcp-Session-Id", cls.session)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def mcp():
    _Mcp.tools, _Mcp.answer, _Mcp.error, _Mcp.stream, _Mcp.session, _Mcp.delay = [FIND, OPEN], ANSWER, False, False, "", 0
    _Mcp.asked = []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Mcp)
    thread = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{srv.server_port}/mcp"
    srv.shutdown()
    srv.server_close()


def _calls() -> list:
    return [a for a in _Mcp.asked if a[0] == "tools/call"]


# ---------------------------------------------------------------- the schema → the form

def test_each_type_of_the_schema_gets_its_own_control():
    got = {f.name: f for f in fields(FIND["inputSchema"])}
    assert [f.name for f in fields(FIND["inputSchema"])] == ["term", "top", "mode", "fresh", "piece", "tags", "order"]
    assert {n: f.control for n, f in got.items()} == {"term": "text", "top": "integer", "mode": "select",
                                                      "fresh": "checkbox", "piece": "text", "tags": "json",
                                                      "order": "select"}  # order: an enum behind a $ref
    assert got["term"].required and not got["top"].required
    assert (got["top"].minimum, got["top"].maximum, got["top"].default) == (1, 20, 5)
    assert got["order"].options == ("best", "new") and got["piece"].types == ("string", "integer")


def test_the_form_draws_every_field_with_its_marks():
    html = form_html(FIND, {}, shown=True)
    assert "name='call' value='lookup'" in html and "action='/try#result'" in html
    assert re.search(r"<input type='text' name='a\.term'[^>]* required", html)  # required, and marked
    assert "必填" in html and "data-tip='要找的字'" in html
    assert re.search(r"<input type='number' name='a\.top'[^>]*min='1' max='20' step='1'", html)
    assert "placeholder='預設 5'" in html
    assert "<option value=''>預設（short）</option><option value='short'>short</option>" in html
    assert re.search(r"type='checkbox' name='a\.fresh' value='true' checked", html)  # its default is true
    assert "<textarea name='a.tags'" in html
    assert "<label class='tr-f wide'>" in html  # the question's box takes the whole row


def test_the_form_keeps_what_was_sent_last():
    html = form_html(FIND, {"term": "停修", "top": "3", "mode": "long", "fresh": "false"}, shown=True)
    assert "value='停修'" in html and "value='3'" in html and "<option value='long' selected>" in html
    assert not re.search(r"name='a\.fresh' value='true' checked", html)


def test_arguments_read_each_value_as_its_type_and_leave_out_what_was_left_empty():
    params = QueryParams("a.term=停修&a.top=3&a.mode=&a.fresh=false&a.fresh=true&a.piece=2&a.tags=[\"x\"]&a.order=new")
    assert arguments(FIND["inputSchema"], params) == {"term": "停修", "top": 3, "piece": "2", "tags": ["x"],
                                                      "order": "new"}  # fresh: on, as its default: left out
    off = QueryParams("a.term=x&a.fresh=false")
    assert arguments(FIND["inputSchema"], off) == {"term": "x", "fresh": False}
    bad = QueryParams("a.term=x&a.top=many")  # sent as typed: the agent would get the service's own complaint
    assert arguments(FIND["inputSchema"], bad) == {"term": "x", "top": "many"}
    assert arguments(FIND["inputSchema"], QueryParams("a.term=")) == {}


def test_a_number_schema_reads_numbers_and_refuses_infinity():
    schema = {"type": "object", "properties": {"n": {"type": "number"}}}
    assert arguments(schema, {"a.n": "2.5"}) == {"n": 2.5} and arguments(schema, {"a.n": "4"}) == {"n": 4}
    assert arguments(schema, {"a.n": "inf"}) == {"n": "inf"}


def test_the_tool_that_reads_one_result_again_is_found_in_the_schemas():
    assert follow_up([FIND, OPEN], {"id", "ref", "title"}) == ("open", "ref")
    assert follow_up([FIND, OPEN, WRITE], {"title"}) is None
    assert follow_up([WRITE], {"ref"}) is None  # not read-only: never offered


def test_tokens_are_estimated_wide_characters_one_each():
    assert approx_tokens("") == 0 and approx_tokens("停修申請") == 4 and approx_tokens("abcdef") == 2
    assert approx_tokens("停修 abc") == 2 + 2


# ---------------------------------------------------------------- the page over a made-up service

def test_on_open_the_page_shows_what_the_agent_reads_first(mcp):
    title, body = TryPage(mcp).page({})
    assert title == "搜尋"
    assert "這是編的開場說明。\n第二行。" in body and "made-up 9.9" in body and "2025-06-18" in body
    assert "<details class='tr-first' id='first'>" in body  # folded: the search box comes first
    assert body.index("form class='sr-form") < body.index("id='first'")
    raw = [a for a in _Mcp.asked if a[0] in ("initialize", "tools/list")]
    assert [a[0] for a in raw] == ["initialize", "tools/list"] and not _calls()
    assert "initialize" in body and "tools/list" in body and "找一找測試用的文件。" in body  # the raw and the rendering
    assert "工具 2 個" in body and f"開場說明 {len(HELLO['instructions'])} 字" in body


def test_the_forms_follow_whatever_tools_the_list_names(mcp):
    _Mcp.tools = [{"name": "ask_campus", "description": "編的工具。", "annotations": RO,
                   "inputSchema": {"type": "object", "required": ["words"],
                                   "properties": {"words": {"type": "string"}, "count": {"type": "integer"}}}},
                  {"name": "page_by_key", "description": "編的另一個。", "annotations": RO,
                   "inputSchema": {"type": "object", "required": ["key"], "properties": {"key": {"type": "string"}}}}]
    _, body = TryPage(mcp).page({})
    assert "<form class='sr-form card pad' method='get' action='/try#result' data-tool='ask_campus'>" in body
    assert "name='a.words'" in body and "name='a.count'" in body
    assert "data-tool='page_by_key'" not in body  # reads one result: reached from a result card, not a form
    assert "data-tool='lookup'" not in body and "name='a.query'" not in body
    _Mcp.tools = [*_Mcp.tools, {"name": "count_things", "description": "編的第三個。", "annotations": RO,
                                "inputSchema": {"type": "object", "properties": {"when": {"type": "string"}}}}]
    _, body = TryPage(mcp).page({})
    assert "其他工具 1" in body and "data-tool='count_things'" in body and "name='a.when'" in body
    _, body = TryPage(mcp).page({"call": "ask_campus", "a.words": "測試", "a.count": "2"})
    assert _calls()[-1][1] == {"name": "ask_campus", "arguments": {"words": "測試", "count": 2}}


def test_a_call_shows_the_text_as_received_and_the_same_laid_out(mcp):
    title, body = TryPage(mcp).page(QueryParams("call=lookup&a.term=測試"))
    text = json.dumps(ANSWER, ensure_ascii=False)
    assert title == "測試 · 搜尋"
    assert _calls()[-1][1] == {"name": "lookup", "arguments": {"term": "測試"}}
    called = body[body.index("id='result'"):body.index("id='first'")]  # the answer, before the folded first look
    left, right = called.split("<header>排版</header>")[-2:]
    assert f"<pre class='tr-pre' data-asis>{try_page.e(text)}</pre>" in left  # unchanged
    assert f"{len(text):,} 字" in left and f"約 {approx_tokens(text):,} token" in left
    assert "第一則測試公告" in right and "第二份附件" in right and "2 筆" in right
    assert "<details class='tr-first' id='first'>" in body  # folded under a call
    assert "class='tr-kind tr-k0'" in right and "class='tr-kind tr-k2'" in right  # 公告, 附件 in their kinds' marks
    assert "school.example/news" in right and "同義詞：試驗" in right
    assert "發布 2026-10-01（公告頁日期欄）</span>" in right and ">2026-10-01</span>" not in right  # the date once
    assert "brand_new_key" in right  # a key the page does not know is still shown
    assert "附註 2" in right and "收錄範圍" in right


def test_each_card_reads_itself_again_and_asks_why_it_ranked_there(mcp):
    _, body = TryPage(mcp).page(QueryParams("call=lookup&a.term=測試"))
    assert "href='/try?call=open&amp;a.ref=d1#result'" in body and "讀這一份" in body
    # 「為什麼排在這裡」 is this page's second tab on the same question with that document followed, at its row
    assert "href='/try?tab=why&amp;q=%E6%B8%AC%E8%A9%A6&amp;doc=d1#r-d1'" in body and "為什麼排在這裡" in body
    tabs = re.search(r"<nav class='tr-tabs'.*?</nav>", body).group(0)
    assert "<a aria-current='page'>agent 收到的</a>" in tabs
    assert "href='/try?tab=why&amp;q=%E6%B8%AC%E8%A9%A6#result' data-seam='why'" in tabs


def test_the_second_tab_draws_the_explanation_here_and_calls_no_tool(mcp):
    # structure design 2026-11-17 section 二: 搜尋測試 is 試用's second tab; the explanation is given by the caller
    # (nknu.ops.web draws search_page's body), the service is not called for it
    before = len(_calls())
    title, body = TryPage(mcp).page(QueryParams("tab=why&q=測試"), why=("測試", "<div id='made-up-why'>理由</div>"),
                                    board="<div id='made-up-board'></div>")
    assert len(_calls()) == before and title == "測試 · 搜尋"
    assert "<div id='made-up-why'>理由</div>" in body and "made-up-board" not in body
    tabs = re.search(r"<nav class='tr-tabs'.*?</nav>", body).group(0)
    assert "<a aria-current='page' data-seam='why'>為什麼這樣排</a>" in tabs
    assert "href='/try?call=lookup&amp;a.term=%E6%B8%AC%E8%A9%A6#result'" in tabs  # back: the same question, called
    assert "name='a.term' value='測試'" in body  # the question stands in the search tool's box


def test_the_scoreboard_is_under_the_forms_when_nothing_is_asked(mcp):
    _, idle = TryPage(mcp).page({}, board="<div id='made-up-board'></div>")
    assert idle.index("form class='sr-form") < idle.index("made-up-board")
    _, called = TryPage(mcp).page(QueryParams("call=lookup&a.term=測試"), board="<div id='made-up-board'></div>")
    assert "made-up-board" not in called


def test_the_second_tab_and_the_board_show_while_the_service_is_down():
    page = TryPage("http://127.0.0.1:9/mcp")
    title, body = page.page(QueryParams("tab=why&q=測試"), why=("測試", "<div id='made-up-why'></div>"))
    assert "連不上" in body and "made-up-why" in body and title == "測試 · 搜尋"
    assert "aria-disabled='true' data-tip='搜尋服務沒有回應" in body  # no tool to call: the first tab waits
    assert "<form class='ask tr-alone' action='/try'" in body and "name='q' value='測試'" in body  # a box to ask in
    assert "made-up-board" in page.page({}, board="<div id='made-up-board'></div>")[1]
    assert "made-up-board" in TryPage("").page({}, board="<div id='made-up-board'></div>")[1]


def test_reading_one_again_shows_the_document_and_no_ranking(mcp):
    _Mcp.answer = {"found": True, "id": "d1", "title": "第一則測試公告", "identity": "測試處｜公告",
                   "content_type": "全文", "content": "整份的內文。", "toc": ["一、開頭", "二、結尾"]}
    _, body = TryPage(mcp).page(QueryParams("call=open&a.ref=d1"))
    assert _calls()[-1][1] == {"name": "open", "arguments": {"ref": "d1"}}
    assert "整份的內文。" in body and "目錄 2" in body and "一、開頭" in body
    assert "<a aria-disabled='true' data-seam='why'" in body and "為什麼排在這裡" not in body.split("tr-result")[-1]
    assert "data-tool='open' hidden" not in body  # the form of the tool called is the one shown


def test_an_error_answer_is_said_as_the_agent_gets_it(mcp):
    _Mcp.answer, _Mcp.error = "錯誤 bad_argument：參數不合法：top 應為整數", True
    _, body = TryPage(mcp).page(QueryParams("call=lookup&a.term=x&a.top=many"))
    assert "工具回報錯誤" in body and "錯誤 bad_argument：參數不合法：top 應為整數" in body
    assert _calls()[-1][1]["arguments"] == {"term": "x", "top": "many"}


def test_a_tool_not_marked_read_only_is_never_called(mcp):
    _Mcp.tools = [FIND, WRITE]
    _, body = TryPage(mcp).page(QueryParams("call=erase&a.ref=d1"))
    assert not _calls() and "沒有標成只讀" in body
    assert re.search(r"data-tool='erase'><input[^>]*><fieldset disabled>", body)  # shown, and closed
    _, body = TryPage(mcp).page(QueryParams("call=nothing_like_it"))
    assert not _calls() and "沒有這個工具" in body


def test_one_call_at_a_time_a_second_is_told_not_queued(mcp):
    page = TryPage(mcp)
    started, release = threading.Event(), threading.Event()
    real = page.client.call

    def slow(*a, **kw):
        started.set()
        release.wait(5)
        return real(*a, **kw)

    page.client.call = slow
    first: list = []
    t = threading.Thread(target=lambda: first.append(page.page(QueryParams("call=lookup&a.term=一"))))
    t.start()
    assert started.wait(5)
    _, body = page.page(QueryParams("call=lookup&a.term=二"))
    assert "正在查" in body and "另一個查詢還沒查完" in body
    release.set()
    t.join(5)
    assert len(_calls()) == 1 and _calls()[0][1]["arguments"] == {"term": "一"} and "第一則測試公告" in first[0][1]
    _, body = page.page(QueryParams("call=lookup&a.term=三"))  # free again
    assert "正在查" not in body and len(_calls()) == 2


def test_a_call_that_takes_too_long_says_so_and_frees_the_page(mcp):
    _Mcp.delay = 1.0
    page = TryPage(mcp, client=McpClient(mcp, call_seconds=0.2))
    _, body = page.page(QueryParams("call=lookup&a.term=慢"))
    assert "沒拿到結果" in body and "秒內沒有回應" in body
    assert page.busy.acquire(blocking=False)
    page.busy.release()


def test_not_configured_and_not_answering_are_said(mcp):
    title, body = TryPage("").page({})
    assert title == "搜尋" and "沒有設定" in body and "--mcp-url" in body
    _, body = TryPage("http://127.0.0.1:9/mcp").page({})
    assert "連不上" in body and "沒有設定" not in body


def test_an_event_stream_and_a_session_are_followed(mcp):
    _Mcp.stream, _Mcp.session = True, "s-123"
    _, body = TryPage(mcp).page(QueryParams("call=lookup&a.term=測試"))
    assert "第一則測試公告" in body
    methods = [(a[0], a[2]) for a in _Mcp.asked]
    assert methods == [("initialize", ""), ("notifications/initialized", "s-123"), ("tools/list", "s-123"),
                       ("tools/call", "s-123")]


def test_the_exchange_keeps_a_json_rpc_error():
    got = Exchange(method="tools/list", sent={}, message={"jsonrpc": "2.0", "id": 1, "result": {}}, error="x")
    assert got.result is None and not got.ok


# ---------------------------------------------------------------- the route and the option

@pytest.fixture
def served(db, tmp_path, mcp):
    app = web.create_app(tmp_path / "t.db", tmp_path, models=models(), today_fn=lambda: TODAY, mcp_url=mcp)
    with TestClient(app, base_url="http://127.0.0.1:8878") as client:
        yield client


def test_the_route_serves_the_page_with_its_tab_marked(served):
    got = served.get("/try")
    assert got.status_code == 200 and "aria-current='page'>搜尋</a>" in got.text and "data-tool='lookup'" in got.text
    got = served.get("/try", params={"call": "lookup", "a.term": "測試"})
    assert got.status_code == 200 and "第一則測試公告" in got.text
    assert served.post("/try", data={"call": "lookup"}).status_code == 405
    assert served.get("/try", headers={"host": "evil.example:8878"}).status_code == 403


def test_the_route_without_an_address_says_it_is_not_set(db, tmp_path):
    app = web.create_app(tmp_path / "t.db", tmp_path, models=models(), today_fn=lambda: TODAY)
    with TestClient(app, base_url="http://127.0.0.1:8878") as client:
        got = client.get("/try")
    assert got.status_code == 200 and "沒有設定" in got.text


def test_the_address_is_the_option_else_the_watched_mcp_service():
    watch = [("資料庫", "http://datasette:8001", "datasette"), ("搜尋服務（MCP）", "http://mcp:8766/mcp", "mcp")]
    assert web.mcp_url_of("http://other:1/mcp", watch) == "http://other:1/mcp"
    assert web.mcp_url_of("", watch) == "http://mcp:8766/mcp"
    assert web.mcp_url_of("", watch[:1]) == ""


def test_the_bar_has_the_page_right_after_tonight():
    from nknu.ops.theme import bar

    keys = [p["key"] for p in bar.panels()["pages"]]
    assert keys == ["tonight", "sites", "try", "doc"]  # structure design 2026-11-17 section 二


def test_the_lab_compose_passes_the_address():
    import yaml

    from tests.ops.test_deploy_files import KIT

    ops = " ".join(yaml.safe_load((KIT / "compose.yaml").read_text(encoding="utf-8"))["services"]["ops"]["command"])
    assert "--mcp-url http://mcp:8766/mcp" in ops


# ---------------------------------------------------------------- the look (the shared theme's rules)

def test_the_page_style_keeps_the_panels_rules():
    css = try_page.CSS
    assert not re.search(r"#[0-9a-fA-F]{3,6}\b", css)  # colours only from theme.css
    sizes = set(re.findall(r"font-size:\s*([0-9.]+px)", css)) | set(
        re.findall(r"font:\s*(?:[0-9]{3}\s+)?([0-9.]+px)", css))
    assert sizes <= SIZES, sizes - SIZES
    defined = set(re.findall(r"(--[a-z0-9-]+):", THEME))
    assert set(re.findall(r"var\((--[a-z0-9-]+)", css)) <= defined
    selectors = [s.strip() for block in re.findall(r"([^{}]+)\{", css) for s in block.split(",") if s.strip()]
    mine = ("tr-", "sr-")  # this page's own classes only, so nothing leaks into the shared theme
    assert all(any(m in s for m in mine) for s in selectors if not s.startswith("@")), [
        s for s in selectors if not any(m in s for m in mine)]
    assert "--k" not in css.replace("var(--k0)", "").replace("var(--k1)", "").replace("var(--k2)", "").replace(
        "var(--k3)", "")  # kinds' hues only on the kinds' marks
    assert "title=" not in try_page.form_html(FIND, {}, shown=True)  # detail floats in data-tip, never title=
