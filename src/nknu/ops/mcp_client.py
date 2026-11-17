"""A small MCP client for 試用 (nknu.ops.try_page): the same three requests a student's AI agent sends to the search
service, initialize, tools/list and tools/call, over streamable HTTP, with what came back kept exactly as it came.

Design: docs/superpowers/specs/2026-11-17-panels-structure-design.md section 三.1 (owner: 「真实的user agent看见的东西是
什么样的，把他坐在一个网页上 让我尝试用user agent的角度去用这个工具，然后来看具体返回的东西」); redesign 2026-11-17
(research/dashboard_redesign_2026-11-17/diagnosis.md). 【Claude】

Standard library only, as nknu.ops.pipeline's probe of the same service (its _ask): one POST of a JSON-RPC message, the
Host header naming this machine (the service's Host/Origin guard answers no other name; the ops container reaches it as
http://mcp:8766/mcp). The lab's service is stateless and answers JSON (nknu.retrieval.server: stateless_http,
json_response); a server that answers an event stream or hands out a session id is followed too (the session id is
sent back and the initialized notification sent, as the MCP lifecycle asks), so the page keeps working if that
changes. Nothing here is called but these three methods and the initialized notification: no resource, no prompt, no
method that could change anything.
"""

from __future__ import annotations

import json
import math
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from urllib.parse import urlsplit

PROTOCOL = "2025-06-18"  # the protocol version this client offers (the one nknu.ops.pipeline offers too)
CLIENT = {"name": "nknu-try-page", "version": "1"}
HELLO_SECONDS = 10.0  # initialize and tools/list load no model 【Claude】
CALL_SECONDS = 60.0  # a tools/call; the first after a restart loads both models (about 30 s) 【Claude】
MAX_BYTES = 8_000_000  # the most of one answer read (a search answer is about 30 KB) 【Claude】
# what counts as one token in the rough estimate: a CJK or full-width character is about one token in the tokenizers
# agents run on, anything else about a third of one (JSON's quotes, digits, URLs) 【Claude】
_WIDE = re.compile(r"[⺀-鿿가-힯豈-﫿︰-﹏＀-￯\U00020000-\U0003134f]")
OTHER_PER_TOKEN = 3.0


def approx_tokens(text: str) -> int:
    """About how many tokens text is to a model: wide characters one each, the rest OTHER_PER_TOKEN to one. 【Claude】:
    an estimate only (no tokenizer is bundled, and each agent's model has its own)."""
    if not text:
        return 0
    wide = len(_WIDE.findall(text))
    return wide + math.ceil((len(text) - wide) / OTHER_PER_TOKEN)


@dataclass
class Exchange:
    """One request and what came back. raw is the body exactly as received (UTF-8; a byte that is not becomes U+FFFD);
    message the JSON-RPC response read from it (None when there is none); error, '' or why there is no usable result
    (no answer, an HTTP error, a JSON-RPC error)."""

    method: str
    sent: dict
    status: int | None = None
    raw: str = ""
    ms: float = 0.0
    message: dict | None = None
    error: str = ""
    session: str = ""
    kind: str = ""  # the answer's Content-Type

    @property
    def result(self) -> dict | None:
        got = (self.message or {}).get("result")
        return got if isinstance(got, dict) and not self.error else None

    @property
    def ok(self) -> bool:
        return self.result is not None


@dataclass
class Session:
    """What one look at the service gives: the handshake, the tool list, and the session id when the server keeps
    sessions (a stateless one gives none)."""

    hello: Exchange
    listing: Exchange | None = None
    session: str = ""
    tools: list[dict] = field(default_factory=list)


def local_host(url: str) -> str:
    """The Host the service answers: this machine's name with the URL's port (as nknu.ops.pipeline._local_host)."""
    parts = urlsplit(url)
    return f"127.0.0.1:{parts.port or (443 if parts.scheme == 'https' else 80)}"


def _messages(raw: str, kind: str) -> list[dict]:
    """The JSON-RPC messages of an answer: the body itself, or each event's data of an event stream."""
    if kind.startswith("text/event-stream"):
        out = []
        for block in re.split(r"\r?\n\r?\n", raw):
            data = "\n".join(line[5:].lstrip(" ") for line in block.splitlines() if line.startswith("data:"))
            if data.strip():
                try:
                    got = json.loads(data)
                except ValueError:
                    continue
                out.extend(got if isinstance(got, list) else [got])
        return [m for m in out if isinstance(m, dict)]
    try:
        got = json.loads(raw) if raw.strip() else None
    except ValueError:
        return []
    items = got if isinstance(got, list) else [got]
    return [m for m in items if isinstance(m, dict)]


class McpClient:
    """The three requests against one service address (e.g. http://mcp:8766/mcp)."""

    def __init__(self, url: str, *, hello_seconds: float = HELLO_SECONDS, call_seconds: float = CALL_SECONDS) -> None:
        self.url = url
        self.hello_seconds, self.call_seconds = hello_seconds, call_seconds
        self._next = 0

    def _id(self) -> int:
        self._next += 1
        return self._next

    def _post(self, body: dict, *, timeout: float, session: str = "") -> Exchange:
        headers = {"Host": local_host(self.url), "Content-Type": "application/json",
                   "Accept": "application/json, text/event-stream", "MCP-Protocol-Version": PROTOCOL}
        if session:
            headers["Mcp-Session-Id"] = session
        got = Exchange(method=str(body.get("method", "")), sent=body)
        started = time.monotonic()
        req = urllib.request.Request(self.url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                                     method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                got.status = resp.status
                got.kind = resp.headers.get("Content-Type", "") or ""
                got.session = resp.headers.get("Mcp-Session-Id", "") or ""
                got.raw = resp.read(MAX_BYTES).decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:  # it answered, with an error status: keep what it said
            got.status = exc.code
            got.kind = exc.headers.get("Content-Type", "") if exc.headers else ""
            try:
                got.raw = exc.read(MAX_BYTES).decode("utf-8", "replace")
            except OSError:
                got.raw = ""
            got.error = f"服務回 HTTP {exc.code}"
        except TimeoutError:
            got.error = f"{timeout:.0f} 秒內沒有回應"
        except (urllib.error.URLError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            got.error = (f"{timeout:.0f} 秒內沒有回應" if isinstance(reason, TimeoutError)
                         else f"連不上（{type(reason).__name__}）")
        got.ms = (time.monotonic() - started) * 1000
        if "id" in body:
            found = [m for m in _messages(got.raw, got.kind) if m.get("id") == body["id"]]
            got.message = found[0] if found else None
            if got.message is not None and isinstance(got.message.get("error"), dict):
                err = got.message["error"]
                got.error = f"JSON-RPC 錯誤 {err.get('code', '')}：{err.get('message', '')}".strip()
            elif got.message is None and not got.error:
                got.error = "回應不是 JSON-RPC" if got.raw.strip() else "回應是空的"
        return got

    def hello(self) -> Exchange:
        """initialize, as an agent's client sends it first."""
        body = {"jsonrpc": "2.0", "id": self._id(), "method": "initialize",
                "params": {"protocolVersion": PROTOCOL, "capabilities": {}, "clientInfo": CLIENT}}
        return self._post(body, timeout=self.hello_seconds)

    def open(self) -> Session:
        """initialize, the initialized notification when the server keeps sessions, then tools/list."""
        hello = self.hello()
        out = Session(hello=hello, session=hello.session)
        if not hello.ok:
            return out
        if out.session:
            self._post({"jsonrpc": "2.0", "method": "notifications/initialized"}, timeout=self.hello_seconds,
                       session=out.session)
        out.listing = self._post({"jsonrpc": "2.0", "id": self._id(), "method": "tools/list", "params": {}},
                                 timeout=self.hello_seconds, session=out.session)
        tools = (out.listing.result or {}).get("tools")
        out.tools = [t for t in tools if isinstance(t, dict) and t.get("name")] if isinstance(tools, list) else []
        return out

    def call(self, name: str, arguments: dict, *, session: str = "") -> Exchange:
        """tools/call of one tool with these arguments, as an agent sends it."""
        body = {"jsonrpc": "2.0", "id": self._id(), "method": "tools/call",
                "params": {"name": name, "arguments": arguments}}
        return self._post(body, timeout=self.call_seconds, session=session)
