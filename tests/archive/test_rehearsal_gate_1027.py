"""The rehearsal as a gate (design §23 E1, E2, 【Claude 的执行办法】, review of 830c1b7): 「彩排 94/94」 was a --check of
a rehearsal folder an older version of the crawler had made (rehearsal.check reads the frontier.db it finds and runs
no code of the crawler's), so it proved nothing of the code under review.

- E1: a rehearsal writes the version of the code that made it (meta rehearsal:code: `git describe --always --dirty`,
  and when the tree is dirty a digest of what differs under src/ and config/, else `unknown`); `rehearse --check`
  prints it beside the version now and fails the whole check (exit 2, no item read) when they differ, when the folder
  has none, or when either cannot be read. `rehearse --old … --dir … --check … --fresh` makes a new rehearsal and checks
  it in one step, and refuses a --dir that is already there.
- E2: rehearsal_expect.json's external_fn_unknown no longer lists scrollToTop and toggleMenu (R7 read script.js, the
  platform file ignores them, design §22 F4); a new item checks they are ignored, and nothing else is loosened.

Offline: the small old archive of test_rehearsal, its plan; no person's name."""

from __future__ import annotations

import functools
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from nknu.admin.cli import app
from nknu.archive import google_api, rehearsal
from nknu.archive.frontier import Frontier
from tests.archive.test_rehearsal import GOOD, PLAN, _expect, jstest_dir, make_old

EXPECT = Path(__file__).resolve().parents[2] / "research" / "project_audit_2026-10-25" / "rehearsal_expect.json"


@pytest.fixture
def small(monkeypatch, tmp_path):
    """The command's rehearse on the small old archive and its plan (the real one reads the round's whole list)."""
    monkeypatch.delenv(google_api.KEY_ENV, raising=False)
    real = rehearsal.rehearse
    monkeypatch.setattr(rehearsal, "rehearse", functools.partial(real, plan=PLAN, cc_dir=None, probe=None,
                                                                 jstest=jstest_dir(tmp_path)))
    return make_old(tmp_path / "old")


def _meta(root: Path, key: str) -> str | None:
    f = Frontier(root / "frontier.db", readonly=True)
    try:
        return f.meta(key)
    finally:
        f.close()


def test_the_code_version_is_read_from_git_and_says_what_is_uncommitted():
    v = rehearsal.code_version()
    assert v != "unknown" and v  # the tests run in the repository
    assert v == rehearsal.code_version()  # the same code, the same version
    if "-dirty" in v:
        assert "+" in v.split("-dirty", 1)[1]  # dirty: which uncommitted changes, not only that there are some


def test_a_rehearsal_writes_the_code_that_made_it_and_a_resumed_one_keeps_it(small, tmp_path, monkeypatch):
    root = tmp_path / "new"
    done = rehearsal.rehearse(small, root)
    assert _meta(root, rehearsal.CODE_KEY) == rehearsal.code_version()
    assert rehearsal.code_version() in done.summary.read_text(encoding="utf-8")
    monkeypatch.setattr(rehearsal, "code_version", lambda: "0000000-dirty+changed")
    rehearsal.rehearse(small, root)  # goes on in its own folder: the version that seeded it stays
    assert _meta(root, rehearsal.CODE_KEY) != "0000000-dirty+changed"


def test_check_fails_whole_when_the_folder_was_made_by_other_code_or_says_nothing(small, tmp_path, monkeypatch):
    root = tmp_path / "new"
    rehearsal.rehearse(small, root)
    good = _expect(tmp_path / "good.json", GOOD)
    out = CliRunner().invoke(app, ["archive", "rehearse", "--check", str(good), "--dir", str(root)])
    assert out.exit_code == 0, out.output
    assert f"彩排库的程式版本：{rehearsal.code_version()}" in out.output
    monkeypatch.setattr(rehearsal, "code_version", lambda: "1234567-dirty+other")
    out = CliRunner().invoke(app, ["archive", "rehearse", "--check", str(good), "--dir", str(root)])
    assert out.exit_code == 2
    assert "FAIL" in out.output and "用现在的程式全新彩排再 --check" in out.output
    assert not any(x.startswith("PASS  ") for x in out.output.splitlines())  # no item read: nothing looks passed
    monkeypatch.undo()
    f = Frontier(root / "frontier.db")
    f.x("DELETE FROM meta WHERE key=?", (rehearsal.CODE_KEY,))  # a folder an older version made
    f.close()
    out = CliRunner().invoke(app, ["archive", "rehearse", "--check", str(good), "--dir", str(root)])
    assert out.exit_code == 2 and "没有记下" in out.output


def test_a_version_that_cannot_be_read_is_no_pass(small, tmp_path, monkeypatch):
    monkeypatch.setattr(rehearsal, "code_version", lambda: "unknown")
    root = tmp_path / "new"
    rehearsal.rehearse(small, root)
    out = CliRunner().invoke(app, ["archive", "rehearse", "--check", str(_expect(tmp_path / "g.json", GOOD)),
                                   "--dir", str(root)])
    assert out.exit_code == 2 and "读不到" in out.output


def test_fresh_makes_a_new_rehearsal_and_checks_it_in_one_step(small, tmp_path):
    root = tmp_path / "fresh"
    good = _expect(tmp_path / "good.json", GOOD)
    out = CliRunner().invoke(app, ["archive", "rehearse", "--old", str(small), "--dir", str(root), "--check", str(good),
                                   "--fresh"])
    assert out.exit_code == 0, out.output
    assert sum(x.startswith("PASS  ") for x in out.output.splitlines()) == len(GOOD)
    assert _meta(root, rehearsal.CODE_KEY) == rehearsal.code_version()
    again = CliRunner().invoke(app, ["archive", "rehearse", "--old", str(small), "--dir", str(root), "--check",
                                     str(good), "--fresh"])
    assert again.exit_code == 1 and "已经存在" in again.output  # never on a folder already there
    empty = tmp_path / "empty"
    empty.mkdir()
    out = CliRunner().invoke(app, ["archive", "rehearse", "--old", str(small), "--dir", str(empty), "--check",
                                   str(good), "--fresh"])
    assert out.exit_code == 1 and "已经存在" in out.output
    out = CliRunner().invoke(app, ["archive", "rehearse", "--dir", str(tmp_path / "x"), "--check", str(good), "--fresh"])
    assert out.exit_code == 1 and "--old" in out.output


def test_the_refusal_on_a_rehearsal_folder_says_to_check_a_fresh_rehearsal(small, tmp_path):
    root = tmp_path / "new"
    rehearsal.rehearse(small, root)
    out = CliRunner().invoke(app, ["archive", "run", "--dir", str(root)])
    assert out.exit_code == 1 and "全新彩排" in out.output and "--check" in out.output


# ---------------------------------------------------------------- E2: the expectation list

# the other 93 items as they were before §23 E2 (sha256 of their JSON, keys sorted): nothing else may be loosened
REST_BEFORE = "fb1df1d40e5493cecc88c1d827e769932d7da6333574f4e9afa4ecac0d563871"


def _items() -> dict[str, dict]:
    if not EXPECT.is_file():
        pytest.skip("research/ is not in git: the expectation list is only on the maintainer's machine")
    return {it["id"]: it for it in json.loads(EXPECT.read_text(encoding="utf-8"))["items"]}


def test_the_functions_r7_decided_are_no_longer_expected_undecided_and_are_checked_as_ignored():
    items = _items()
    unknown = items["external_fn_unknown"]
    assert "js_link:scrollToTop" not in unknown["select"]["params"]
    assert "js_link:toggleMenu" not in unknown["select"]["params"]
    assert unknown["select"]["sql"].count("?") == len(unknown["select"]["params"]) == unknown["expect"]["n"] == 10
    assert unknown["expect"]["each"] == {"need": ["must"], "why": ["unhandled:unknown"]}  # not loosened
    assert "10-27" in unknown["依据"] and "§23 E2" in unknown["依据"]
    decided = items["external_fn_decided_ignore"]
    assert sorted(decided["select"]["params"]) == ["js_link:scrollToTop", "js_link:toggleMenu"]
    assert decided["expect"] == {"n": 2, "each": {"need": ["no"], "why": ["unhandled:ignore"]}}


def test_the_rest_of_the_expectation_list_is_as_it_was():
    """Nothing else loosened (design §23 E2): every other item word for word as before."""
    import hashlib

    rest = [it for it in _items().values() if it["id"] not in ("external_fn_unknown", "external_fn_decided_ignore")]
    got = hashlib.sha256(json.dumps(rest, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    assert len(rest) == 93 and got == REST_BEFORE
