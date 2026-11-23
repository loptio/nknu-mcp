"""The machine-wide lock (round-1 design §6.9, review 10-26 C, B5): one program at a time sends requests to the school.
`archive run`, jscheck and jsfetch hold it; fetch.Client itself refuses a request to a school host (nknu.edu.tw,
140.127.*) on the real network unless this process holds it, so a stray script is stopped too. The lock is a flock on
NKNU_SCHOOL_LOCK (tests/conftest.py points it at a temporary file)."""

from __future__ import annotations

import os

import httpx
import pytest

from nknu.archive import schoollock
from nknu.archive.fetch import Client, FetchError
from nknu.archive.target import RateLimit, Target

FAST = RateLimit(min_interval_s=0, jitter_s=0)


def test_the_tests_never_use_the_real_lock_file():
    assert schoollock.lock_path() != schoollock.DEFAULT_PATH
    assert os.environ["NKNU_SCHOOL_LOCK"] == str(schoollock.lock_path())


def test_one_holder_at_a_time_and_it_lets_go():
    assert not schoollock.held() and schoollock.holder() is None
    lock = schoollock.hold()
    try:
        assert schoollock.held() and schoollock.holder() == str(os.getpid())
        with pytest.raises(schoollock.SchoolLockBusy) as busy:
            schoollock.hold()  # a second open file is a second holder, even in this process
        assert busy.value.pid == str(os.getpid())
    finally:
        lock.close()
    assert not schoollock.held() and schoollock.holder() is None
    schoollock.hold().close()  # free again


@pytest.mark.parametrize("host", ["c.nknu.edu.tw", "nknu.edu.tw", "140.127.40.173", "sso.NKNU.edu.tw"])
def test_school_hosts(host):
    assert schoollock.is_school_host(host)


@pytest.mark.parametrize("host", ["www.googleapis.com", "nknu.edu.tw.example.com", "140.128.1.1", "127.0.0.1", ""])
def test_not_school_hosts(host):
    assert not schoollock.is_school_host(host)


def test_the_client_refuses_a_school_host_without_the_lock():
    c = Client(sleep=lambda s: None, attempts=1)
    try:
        with pytest.raises(schoollock.SchoolLockNotHeld):
            c.fetch(Target("https://c.nknu.edu.tw/aca/"), FAST)
        with pytest.raises(schoollock.SchoolLockNotHeld):
            c.fetch(Target("http://140.127.62.110/"), FAST)
        lock = schoollock.hold()
        try:  # with the lock it goes on, and meets the test network guard instead
            with pytest.raises(FetchError, match="test guard"):
                c.fetch(Target("https://c.nknu.edu.tw/aca/"), FAST)
        finally:
            lock.close()
        with pytest.raises(FetchError, match="test guard"):  # not the school's: no lock needed
            c.fetch(Target("https://www.example.org/"), FAST)
    finally:
        c.close()


def test_a_client_on_a_test_transport_never_reaches_a_server_and_needs_no_lock():
    c = Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, text="ok")), sleep=lambda s: None)
    try:
        assert c.fetch(Target("https://c.nknu.edu.tw/aca/"), FAST).status == 200
    finally:
        c.close()

