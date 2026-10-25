"""Archive tests talk to servers of their own on this machine only. The guard that refuses every other name lookup,
connection and datagram is tests/conftest.py's, for the whole run (review 10-26: it used to live here, one test
function at a time). What is left here: a local test server is asked directly, never through a proxy from the
environment."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_proxy(monkeypatch):
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(var, raising=False)
