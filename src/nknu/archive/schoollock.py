"""The machine-wide lock on requests to the school (round-1 design §1.4, §6.9; review 10-26 C, B5): one program at a
time sends requests to the school's servers, whatever it is (the archive crawl, jscheck, jsfetch, a script someone
writes). It is an flock on settings.school_lock (NKNU_SCHOOL_LOCK, else
~/.cache/nknu/school-requests.lock): the kernel lets go when the process ends, kill -9 included, so a stale lock
never stays behind (a pid file would).

fetch.Client asks require() before every request to a school host on the real network: a program that does not
hold the lock is refused before anything is sent, so a forgotten script cannot slip past the crawl's politeness.
Which programs take it is theirs to decide (hold()); a run of the archive crawl holds it for its whole life.
【Claude 的执行办法】(design §6.9 says the archive commands and v1 take it; the check inside the client is review C).

Every program must take the same file (second review 10-26). A lock file another user made and this one may only read
is a lock all the same: flock needs no write access (the holder's pid is then not written). 【Claude 的执行办法】"""

from __future__ import annotations

import fcntl
import ipaddress
import os
import threading
from pathlib import Path
from typing import Self

from nknu.settings import load_settings

DEFAULT_PATH = Path.home() / ".cache" / "nknu" / "school-requests.lock"
SCHOOL_NET = ipaddress.ip_network("140.127.0.0/16")
_held: dict[Path, object] = {}  # the lock files this process holds, by path
_guard = threading.Lock()


class SchoolLockBusy(RuntimeError):
    """Another program (pid) holds the lock: this one must not send anything to the school."""

    def __init__(self, pid: str, path: Path):
        super().__init__(f"another program sends requests to the school right now (pid {pid}, lock {path})")
        self.pid, self.path = pid, path


class SchoolLockNotHeld(RuntimeError):
    """A request to a school host from a process that does not hold the lock: refused before it is sent."""


def lock_path() -> Path:
    return load_settings().school_lock


def is_school_host(host: str | None) -> bool:
    """A host of the school's own servers: nknu.edu.tw and its subdomains, and the school's 140.127.* addresses (a
    Google Site built with school accounts is Google's server, not the school's)."""
    h = (host or "").lower().rstrip(".").strip("[]")
    if not h:
        return False
    if h == "nknu.edu.tw" or h.endswith(".nknu.edu.tw"):
        return True
    try:
        return ipaddress.ip_address(h) in SCHOOL_NET
    except ValueError:
        return False


class SchoolLock:
    """The lock as held; close() lets go (the process ending does too)."""

    def __init__(self, path: Path, fh) -> None:
        self.path, self._fh = path, fh

    def close(self) -> None:
        with _guard:
            if _held.get(self.path) is self:
                del _held[self.path]
        if not self._fh.closed:
            self._fh.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def hold(path: Path | None = None) -> SchoolLock:
    """Take the lock, or raise SchoolLockBusy with the holder's pid. A second hold in a process that holds it is
    refused too: two parts of one program each sending would be two senders. A new lock file is made readable and
    writable by its group (0664 before the umask), so that two users of one group share it."""
    path = Path(path or lock_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fh = os.fdopen(os.open(path, os.O_RDWR | os.O_CREAT, 0o664), "r+")
        writable = True
    except PermissionError:  # another user's lock file this one may only read: flock needs no write access
        fh = open(path)  # noqa: SIM115 - held until close(); the kernel lets go when the process ends
        writable = False
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fh.seek(0)
        pid = fh.read().strip() or "?"
        fh.close()
        raise SchoolLockBusy(pid, path) from None
    if writable:
        fh.seek(0)
        fh.truncate()
        fh.write(str(os.getpid()))
        fh.flush()
    lock = SchoolLock(path, fh)
    with _guard:
        _held[path] = lock
    return lock


def held(path: Path | None = None) -> bool:
    """Whether this process holds the lock."""
    with _guard:
        return Path(path or lock_path()) in _held


def holder(path: Path | None = None) -> str | None:
    """The pid written in the lock while some process holds it; None when nobody does."""
    path = Path(path or lock_path())
    if Path(path) in _held:
        return str(os.getpid())
    if not path.exists():
        return None
    with open(path) as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            return fh.read().strip() or "?"
        fcntl.flock(fh, fcntl.LOCK_UN)
        return None


def require(host: str | None) -> None:
    """Refuse a request to a school host unless this process holds the lock."""
    if is_school_host(host) and not held():
        raise SchoolLockNotHeld(f"{host}: this process does not hold the machine-wide lock on school requests "
                                f"({lock_path()}); only the program that holds it may send to the school")
