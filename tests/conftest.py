"""Shared test setup. No test reaches a real server: from the moment pytest starts (pytest_configure, before any
fixture, session ones included, and in every thread a test starts) name lookups, connections and datagrams that do
not go to this machine are refused with "test guard", in every test directory. Only a test marked live (tests/live,
which the default `-m 'not live'` leaves out) runs without it. Review 10-26: the guard used to be tests/archive's alone,
one test function at a time, and did not cover sendto or sendmsg. The machine-wide lock on school requests
(NKNU_SCHOOL_LOCK, design §6.9) is a file of the test run's own, never ~/.cache/nknu's."""

from __future__ import annotations

import ipaddress
import os
import shutil
import socket
import tempfile
from pathlib import Path

import pytest
import stamina

_REAL: dict[tuple[object, str], object] = {}
_TESTS = Path(__file__).resolve().parent
_NO_MODELS_IN = (_TESTS / "content", _TESTS / "retrieval")


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
    # the machine-wide lock on school requests (round-1 design §6.9): never the real one in ~/.cache, whoever holds it
    config._nknu_lock_dir = tempfile.mkdtemp(prefix="nknu-school-lock-")
    config._nknu_lock_env = os.environ.get("NKNU_SCHOOL_LOCK")
    os.environ["NKNU_SCHOOL_LOCK"] = os.path.join(config._nknu_lock_dir, "school-requests.lock")


def pytest_unconfigure(config):
    _set(_REAL)
    if getattr(config, "_nknu_lock_env", None) is None:
        os.environ.pop("NKNU_SCHOOL_LOCK", None)
    else:
        os.environ["NKNU_SCHOOL_LOCK"] = config._nknu_lock_env
    if getattr(config, "_nknu_lock_dir", None):
        shutil.rmtree(config._nknu_lock_dir, ignore_errors=True)


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
def no_real_models(request):
    """In tests/content and tests/retrieval the four scoring-model loaders raise ModelUnavailable unless a test is
    marked models: both models sit in this machine's cache, so a forgotten fake would load 2.3 GB. Code calls them
    through the module (models.shared_embedder()), which is where they are replaced. The test's own monkeypatch holds
    the replacement, so patches a test adds on top are undone in the right order."""
    path = Path(str(request.node.path)).resolve()
    if request.node.get_closest_marker("models") is not None or not any(path.is_relative_to(d) for d in _NO_MODELS_IN):
        yield
        return
    from nknu.core import models

    def refuse(*_args, **_kwargs):
        raise models.ModelUnavailable("unit tests never load real models")

    mp = request.getfixturevalue("monkeypatch")
    for name in ("load_embedder", "load_reranker", "shared_embedder", "shared_reranker"):
        mp.setattr(models, name, refuse)
    yield


@pytest.fixture(autouse=True)
def _no_real_backoff():
    # keep the configured number of attempts, but never sleep for real in tests
    with stamina.set_testing(True, attempts=4, cap=True):
        yield

