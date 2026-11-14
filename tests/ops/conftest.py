"""The fixture the tests of tests/ops share (plan 2026-11-15): one made-up library per test (tests.ops.library)."""

from __future__ import annotations

import pytest

from nknu.retrieval import vectors
from tests.ops.library import fill
from tests.retrieval.test_hybrid import Tiny


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(vectors, "_CACHE", {})  # one index per database file and process: none from another test
    t = fill(Tiny(tmp_path / "t.db"))
    t.conn.commit()
    yield t
    t.conn.close()
