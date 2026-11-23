"""Datasette plugin (plan 2026-11-15 T7; spec 2026-11-15 section 二.1): answer only a request whose Host names this
machine. Datasette listens on every address of its container and Docker forwards only the lab PC's own 127.0.0.1 to
it, but a page of another site, opened in the owner's browser, could still reach that port under a name of its own
(DNS rebinding) and read the library, which holds students' names. The archive viewer (nknu.archive.viewer's
LOCAL_HOSTS) and the MCP server (FastMCP's strict Host guard) refuse such a request the same way 【Claude】.

Loaded by `datasette --plugins-dir`; no other code imports it."""

from __future__ import annotations

try:
    from datasette import hookimpl
except ImportError:  # the tests load this file where Datasette is not installed

    def hookimpl(fn):
        return fn


LOCAL_HOSTS = ("127.0.0.1", "localhost", "[::1]")


def local_host(header: str) -> bool:
    """Whether a Host header names this machine: one of LOCAL_HOSTS, alone or followed by a port, and nothing else
    ('[::1]evil.example' and '127.0.0.1.evil.example' do not). The same check as nknu.ops.web.local_host; this file
    is loaded by Datasette alone and imports nothing of ours."""
    host = header.strip().lower()
    if host.startswith("["):
        name, _, rest = host.partition("]")
        name += "]"
    else:
        name, colon, port = host.partition(":")
        rest = colon + port
    return name in LOCAL_HOSTS and (not rest or (rest[0] == ":" and rest[1:].isascii() and rest[1:].isdigit()))


def local_only(app):
    """The ASGI app that passes a request on only when its Host is one of LOCAL_HOSTS; any other gets 403."""

    async def guarded(scope, receive, send):
        if scope["type"] == "http":
            host = next((value.decode("latin-1") for key, value in scope.get("headers", []) if key == b"host"), "")
            if not local_host(host):
                await send({"type": "http.response.start", "status": 403,
                            "headers": [(b"content-type", b"text/plain; charset=utf-8")]})
                await send({"type": "http.response.body", "body": b"forbidden"})
                return
        await app(scope, receive, send)

    return guarded


@hookimpl
def asgi_wrapper(datasette):
    return local_only
