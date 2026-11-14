"""plan 2026-11-15 T6: the MCP server's --host. In the lab's Docker container the server listens on every address of
the container and Docker forwards only the lab PC's own 127.0.0.1 to it (as the archive viewer does); the Host/Origin
guard stays on wherever it listens, so a page of another site that reaches the port under a name of its own (DNS
rebinding) gets no answer."""

from __future__ import annotations

import sqlite3

import pytest
from fastmcp import FastMCP
from starlette.testclient import TestClient

from nknu.content.schema import create_schema
from nknu.retrieval import server


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "t.db"
    conn = sqlite3.connect(path)
    create_schema(conn)
    conn.commit()
    conn.close()
    return path


def _runs(monkeypatch) -> list:
    runs: list = []
    monkeypatch.setattr(FastMCP, "run", lambda self, transport=None, **kw: runs.append((transport, kw)))
    return runs


def test_host_reaches_the_http_server_with_the_guard_on(db_path, monkeypatch):
    runs = _runs(monkeypatch)
    assert server.main(["--db", str(db_path), "--http", "--host", "0.0.0.0", "--port", "8766"]) == 0
    transport, kw = runs[0]
    assert transport == "http" and kw["host"] == "0.0.0.0" and kw["port"] == 8766
    assert kw["host_origin_protection"] is True


def test_without_host_it_listens_on_loopback_as_before(db_path, monkeypatch):
    runs = _runs(monkeypatch)
    assert server.main(["--db", str(db_path), "--http"]) == 0
    assert runs[0][1]["host"] == "127.0.0.1"


def test_the_guard_answers_only_a_request_that_names_this_machine(db_path):
    """What host_origin_protection=True does in the pinned FastMCP wherever the server listens: another site's name in
    Host is refused before the request reaches a tool. (It answers 127.0.0.1, localhost, [::1] and the address the
    connection came in on; who can connect at all is Docker's 127.0.0.1 publish: review 11-15.)"""
    app = server.create_mcp(db_path).http_app(path="/mcp", stateless_http=True, json_response=True,
                                              host_origin_protection=True)
    call = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    headers = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
    with TestClient(app, base_url="http://127.0.0.1:18766") as client:
        assert client.post("/mcp", json=call, headers=headers).status_code != 421
        assert client.post("/mcp", json=call, headers={**headers, "host": "evil.example:18766"}).status_code == 421
