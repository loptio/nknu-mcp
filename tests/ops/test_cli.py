"""plan 2026-11-15 T4: the two commands, nknu search and nknu trace. On the lab PC they open the library (--db); on the
Mac, where there is none, they ask 检视台 through the tunnel (--at, or the environment's NKNU_OPS_URL) and print the
same."""

from __future__ import annotations

import json

import httpx
import pytest
from typer.testing import CliRunner

from nknu.admin.cli import app
from nknu.ops import cli as ops_cli
from nknu.ops.explain import explain_search, explain_text
from nknu.ops.trace import trace, trace_text
from nknu.retrieval.hybrid import HybridConfig
from tests.ops import world
from tests.ops.world import NOTICE_URL, PAGER_URL
from tests.retrieval.test_search import TODAY

OFF = ["--no-dense", "--no-rerank"]  # no model on the test machine
URL = "http://127.0.0.1:18878"


@pytest.fixture
def run(monkeypatch):
    monkeypatch.delenv("NKNU_OPS_URL", raising=False)
    monkeypatch.setattr(ops_cli, "today_tpe", lambda: TODAY)
    runner = CliRunner()
    return lambda *args, **kw: runner.invoke(app, [str(a) for a in args], **kw)


@pytest.fixture
def path(db, tmp_path):
    return tmp_path / "t.db"


class _Service:
    """Stands in for httpx.get: records what was asked and answers with what it was given."""

    def __init__(self, answer: dict | None = None, status: int = 200, error: Exception | None = None) -> None:
        self.answer, self.status, self.error = answer, status, error
        self.asked: list[tuple[str, dict]] = []

    def __call__(self, url, *, params=None, timeout=None, **kw):
        self.asked.append((url, dict(params or {})))
        self.kw = kw
        if self.error is not None:
            raise self.error
        return httpx.Response(self.status, json=self.answer, request=httpx.Request("GET", url))


# ---------------------------------------------------------------- nknu search

def test_search_explain_prints_the_table(run, path):
    got = run("search", "停修", "--explain", "--db", path, *OFF)
    assert got.exit_code == 0, got.output
    for word in ("问题：停修", "只有关键词", "115學年度第一學期線上申請停修", "名次"):
        assert word in got.output, word


def test_search_explain_follows_documents(run, path):
    got = run("search", "停修", "--explain", "--doc", "dx", "--doc", "none", "--limit", "2", "--db", path, *OFF)
    assert got.exit_code == 0 and "追的文件" in got.output and "库里没有" in got.output and "dx" in got.output


def test_search_explain_json_is_the_report(run, path, db):
    got = run("search", "停修", "--explain", "--json", "--db", path, *OFF)
    want = explain_search(db.conn, "停修", today=TODAY, aliases=[], config=HybridConfig(dense=False, rerank=False))
    assert json.loads(got.output) == json.loads(json.dumps(want))


def test_search_alone_prints_what_the_mcp_tool_answers(run, path):
    got = run("search", "停修", "--db", path, *OFF)
    assert got.exit_code == 0 and json.loads(got.output)["results"][0]["id"]


def test_a_database_that_is_not_there_says_how_to_reach_one(run, tmp_path):
    got = run("search", "停修", "--explain", "--db", tmp_path / "none.db")
    assert got.exit_code == 2 and "找不到内容资料库" in got.output and "NKNU_OPS_URL" in got.output


def test_search_at_asks_the_service_and_prints_the_same(run, db, monkeypatch):
    report = json.loads(json.dumps(explain_search(db.conn, "停修", today=TODAY, aliases=[], follow=["dx"],
                                                  config=HybridConfig(dense=False, rerank=False))))
    service = _Service(report)
    monkeypatch.setattr(httpx, "get", service)
    got = run("search", "停修", "--explain", "--doc", "dx", "--limit", "7", "--at", URL + "/")
    assert got.exit_code == 0 and got.output.strip() == explain_text(report).strip()
    assert service.asked == [(URL + "/api/search", {"q": "停修", "limit": "7", "explain": "1", "doc": ["dx"]})]


def test_the_environment_names_the_service(run, monkeypatch):
    service = _Service({"results": []})
    monkeypatch.setattr(httpx, "get", service)
    got = run("search", "停修", env={"NKNU_OPS_URL": URL})
    assert got.exit_code == 0 and json.loads(got.output) == {"results": []}
    assert service.asked[0] == (URL + "/api/search", {"q": "停修", "limit": "10", "explain": "0", "doc": []})


def test_a_database_given_wins_over_the_environment(run, path, monkeypatch):
    service = _Service({})
    monkeypatch.setattr(httpx, "get", service)
    got = run("search", "停修", "--explain", "--db", path, *OFF, env={"NKNU_OPS_URL": URL})
    assert got.exit_code == 0 and service.asked == [] and "问题：停修" in got.output


def test_a_service_that_does_not_answer_is_exit_2(run, monkeypatch):
    monkeypatch.setattr(httpx, "get", _Service(error=httpx.ConnectError("refused")))
    got = run("search", "停修", "--explain", "--at", URL)
    assert got.exit_code == 2 and "连不上检视台" in got.output and "inspect-tunnel.sh" in got.output


def test_a_service_that_fails_inside_says_what_failed(run, monkeypatch):
    monkeypatch.setattr(httpx, "get", _Service({"error": "TypeError: unhashable type: 'dict'"}, 500))
    got = run("trace", "d3", "--at", URL)
    assert got.exit_code == 2 and "unhashable type" in got.output and "inspect-tunnel.sh" not in got.output


def test_the_model_flags_are_the_services_own(run, monkeypatch):
    monkeypatch.setattr(httpx, "get", _Service({}))
    got = run("search", "停修", "--at", URL, "--no-rerank")
    assert got.exit_code == 2 and "--db" in got.output


# ---------------------------------------------------------------- nknu trace

@pytest.fixture
def made(tmp_path):
    w = world.build(tmp_path)
    yield w
    w.conn.close()


def test_trace_prints_the_five_steps(run, made):
    got = run("trace", NOTICE_URL, "--db", made.db, "--root", made.root)
    assert got.exit_code == 0, got.output
    for word in ("① 抓取", "② 导出", "③ 转文字", "④ 建库", "⑤ 检索", "115-1 停修公告", "第 1 名"):
        assert word in got.output, word


def test_trace_of_what_was_left_out(run, made):
    got = run("trace", PAGER_URL, "--db", made.db, "--root", made.root)
    assert got.exit_code == 0 and "没进库" in got.output


def test_trace_json_is_the_report(run, made):
    got = run("trace", NOTICE_URL, "--json", "--db", made.db, "--root", made.root)
    want = trace(made.conn, NOTICE_URL, root=made.root, today=TODAY)
    assert json.loads(got.output) == json.loads(json.dumps(want))


def test_trace_chunks_says_how_many_to_print(run, made):
    got = run("trace", NOTICE_URL, "--chunks", "1", "--db", made.db, "--root", made.root)
    assert "115學年度第1學期停修申請自11月3日起受理。" in got.output and "逾期不予受理。" not in got.output


def test_trace_at_asks_the_service_and_prints_the_same(run, made, monkeypatch):
    report = json.loads(json.dumps(trace(made.conn, NOTICE_URL, root=made.root, today=TODAY)))
    service = _Service(report)
    monkeypatch.setattr(httpx, "get", service)
    got = run("trace", NOTICE_URL, "--at", URL)
    assert got.exit_code == 0 and got.output.strip() == trace_text(report).strip()
    assert service.asked == [(URL + "/api/trace", {"ref": NOTICE_URL})]


def test_trace_without_a_database_says_how_to_reach_one(run, tmp_path):
    got = run("trace", "d3", "--db", tmp_path / "none.db")
    assert got.exit_code == 2 and "找不到内容资料库" in got.output


# ---------------------------------------------------------------- after the review of 11-15

def test_the_question_goes_straight_to_the_service_never_through_a_proxy(run, monkeypatch):
    """The environment's and the system's proxies are not followed: the question's words stay on this machine and
    the tunnel."""
    service = _Service({"results": []})
    monkeypatch.setattr(httpx, "get", service)
    assert run("search", "停修", "--at", URL).exit_code == 0 and service.kw.get("trust_env") is False


@pytest.mark.parametrize("at", ["http://[::1", "not a url", "ftp://127.0.0.1"])
def test_an_address_that_is_no_service_is_said_in_words(run, at):
    got = run("trace", "d3", "--at", at)
    assert got.exit_code == 2 and "Traceback" not in got.output and "检视台" in got.output


def test_a_file_that_is_no_library_is_said_in_words(run, tmp_path):
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"this is not a database" * 100)
    for args in (("trace", "d3"), ("search", "停修", "--explain"), ("search", "停修")):
        got = run(*args, "--db", bad, *(OFF if args[0] == "search" else ()))
        assert got.exit_code == 2 and "读不了" in got.output and got.exception.__class__ is SystemExit, args


def test_the_remote_answer_is_printed_as_the_local_one_is(run, path, monkeypatch):
    local = run("search", "停修", "--db", path, *OFF).output
    monkeypatch.setattr(httpx, "get", _Service(json.loads(local)))
    assert run("search", "停修", "--at", URL).output == local


def test_the_other_commands_do_not_load_the_http_client():
    """nknu.admin.cli registers the two commands when it is imported: what they alone need loads inside them."""
    import subprocess
    import sys

    code = "import sys, nknu.ops.cli; print('httpx' in sys.modules)"
    got = subprocess.run([sys.executable, "-c", code], check=True, capture_output=True, text=True)
    assert got.stdout.strip() == "False"
