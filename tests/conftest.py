"""Shared test setup. No test reaches a real server: from the moment pytest starts (pytest_configure, before any
fixture, session ones included, and in every thread a test starts) name lookups, connections and datagrams that do
not go to this machine are refused with "test guard", in every test directory. Only a test marked live (tests/live,
which the default `-m 'not live'` leaves out) runs without it. Review 10-26: the guard used to be tests/archive's alone,
one test function at a time, and did not cover sendto or sendmsg."""

from __future__ import annotations

import ipaddress
import socket

import pytest
import stamina

_REAL: dict[tuple[object, str], object] = {}


def _loopback(host) -> bool:
    if str(host).lower() in ("localhost", "localhost.localdomain"):
        return True
    try:
        return ipaddress.ip_address(str(host).split("%", 1)[0]).is_loopback
    except ValueError:
        return False


def _refuse_far(address) -> None:
    """An (host, port, …) address off this machine is refused; a Unix socket's path is local by nature."""
    if isinstance(address, tuple) and address and not _loopback(address[0]):
        raise OSError(f"test guard: nothing is sent to {address[0]!r}")


def _guarded() -> dict[tuple[object, str], object]:
    real_getaddrinfo = socket.getaddrinfo
    real = {name: getattr(socket.socket, name) for name in ("connect", "connect_ex", "sendto", "sendmsg")
            if hasattr(socket.socket, name)}

    def getaddrinfo(host, *args, **kwargs):
        if _loopback(host):
            return real_getaddrinfo(host, *args, **kwargs)
        raise socket.gaierror(socket.EAI_NONAME, f"test guard: no name lookup for {host!r}")

    def by_name(host, *args, **kwargs):
        raise socket.gaierror(socket.EAI_NONAME, f"test guard: no name lookup for {host!r}")

    def connect(self, address):
        _refuse_far(address)
        return real["connect"](self, address)

    def connect_ex(self, address):
        _refuse_far(address)
        return real["connect_ex"](self, address)

    def sendto(self, data, *rest):  # sendto(data, address) or sendto(data, flags, address)
        _refuse_far(rest[-1] if rest else None)
        return real["sendto"](self, data, *rest)

    def sendmsg(self, buffers, *rest):  # sendmsg(buffers[, ancdata[, flags[, address]]])
        _refuse_far(rest[2] if len(rest) > 2 else None)
        return real["sendmsg"](self, buffers, *rest)

    out = {(socket, "getaddrinfo"): getaddrinfo, (socket, "gethostbyname"): by_name,
           (socket, "gethostbyname_ex"): by_name}
    out.update({(socket.socket, name): fn for name, fn in (("connect", connect), ("connect_ex", connect_ex),
                                                            ("sendto", sendto), ("sendmsg", sendmsg))
                if name in real})
    return out


def _set(values: dict[tuple[object, str], object]) -> None:
    for (owner, name), value in values.items():
        setattr(owner, name, value)


def pytest_configure(config):
    guard = _guarded()
    _REAL.update({key: getattr(*key) for key in guard})
    _set(guard)
    config._nknu_guard = guard


def pytest_unconfigure(config):
    _set(_REAL)


@pytest.fixture(autouse=True)
def _network_only_for_live_tests(request):
    """A live test (it reaches a real site on purpose) runs with the real functions, and the guard is back after it."""
    if request.node.get_closest_marker("live") is None:
        yield
        return
    _set(_REAL)
    try:
        yield
    finally:
        _set(request.config._nknu_guard)


@pytest.fixture(autouse=True)
def _no_real_backoff():
    # keep the configured number of attempts, but never sleep for real in tests
    with stamina.set_testing(True, attempts=4, cap=True):
        yield


@pytest.fixture(scope="session")
def world(tmp_path_factory):
    """(facts.db, serve.db) built from every adapter fixture through the real pipeline. Read-only: copy before changing."""
    from tests.world import build_fixture_world

    return build_fixture_world(tmp_path_factory.mktemp("world"))
