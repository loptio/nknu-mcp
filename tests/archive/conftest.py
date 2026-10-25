"""No archive test may reach a real server: name lookups and connections are refused unless they go to this machine's
loopback address (a test's own local server). A test that forgets to stub the resolver or the transport then fails
with "test guard" instead of resolving a school hostname."""

from __future__ import annotations

import ipaddress
import socket

import pytest


def _loopback(host) -> bool:
    try:
        return ipaddress.ip_address(str(host).split("%", 1)[0]).is_loopback
    except ValueError:
        return False


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch):
    real_getaddrinfo = socket.getaddrinfo
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def getaddrinfo(host, *args, **kwargs):
        if _loopback(host):
            return real_getaddrinfo(host, *args, **kwargs)
        raise socket.gaierror(socket.EAI_NONAME, f"test guard: no name lookup for {host!r}")

    def by_name(host, *args, **kwargs):
        raise socket.gaierror(socket.EAI_NONAME, f"test guard: no name lookup for {host!r}")

    def checked(address) -> None:
        if isinstance(address, tuple) and not _loopback(address[0]):
            raise OSError(f"test guard: no connection to {address[0]!r}")

    def connect(self, address):
        checked(address)
        return real_connect(self, address)

    def connect_ex(self, address):
        checked(address)
        return real_connect_ex(self, address)

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(socket, "gethostbyname", by_name)
    monkeypatch.setattr(socket, "gethostbyname_ex", by_name)
    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(var, raising=False)  # a local test server is asked directly, never through a proxy
