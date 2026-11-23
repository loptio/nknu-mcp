"""No test reaches a real server (review 10-26): the guard tests/conftest.py installs when pytest starts refuses every
name lookup and every connection or datagram that does not go to this machine, in every test directory, in session
fixtures and in the threads a test starts, for the whole run but the tests marked live (tests/live)."""

from __future__ import annotations

import socket
import threading

import pytest

FAR = ("192.0.2.1", 9)  # TEST-NET-1: nothing answers there, and nothing may be sent there


@pytest.fixture(scope="session")
def looked_up_in_a_session_fixture():
    try:
        socket.getaddrinfo("www.nknu.edu.tw", 443)
    except OSError as exc:
        return str(exc)
    return "resolved"


def test_a_name_lookup_off_this_machine_is_refused():
    for call in (lambda: socket.getaddrinfo("www.nknu.edu.tw", 443), lambda: socket.gethostbyname("nknu.edu.tw"),
                 lambda: socket.gethostbyname_ex("nknu.edu.tw")):
        with pytest.raises(OSError, match="test guard"):
            call()
    assert socket.getaddrinfo("127.0.0.1", 80) and socket.getaddrinfo("localhost", 80)  # this machine is allowed


def test_a_connection_or_a_datagram_off_this_machine_is_refused():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        for call in (lambda: s.connect(FAR), lambda: s.connect_ex(FAR), lambda: s.sendto(b"x", FAR),
                     lambda: s.sendto(b"x", 0, FAR)):
            with pytest.raises(OSError, match="test guard"):
                call()
        if hasattr(s, "sendmsg"):
            with pytest.raises(OSError, match="test guard"):
                s.sendmsg([b"x"], [], 0, FAR)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:  # this machine is allowed
        s.sendto(b"x", ("127.0.0.1", 9))


def test_the_guard_holds_in_session_fixtures_and_threads(looked_up_in_a_session_fixture):
    assert "test guard" in looked_up_in_a_session_fixture
    seen = []

    def lookup():
        try:
            socket.getaddrinfo("www.nknu.edu.tw", 443)
            seen.append("resolved")
        except OSError as exc:
            seen.append(str(exc))

    t = threading.Thread(target=lookup)
    t.start()
    t.join()
    assert "test guard" in seen[0]
