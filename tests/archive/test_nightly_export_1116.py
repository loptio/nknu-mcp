"""The export batch, T7 (plan docs/superpowers/plans/2026-11-16-export-batch.md; design 2026-11-16-periodic-update-
design.md §7.4, §7.7; the content session's handover points 1, 2 of 11-16): after a night's review, the sites that
changed (an alarmed site never) are exported (update export-list → export_sites.sh, NIGHTLY=1), then seen.tsv is
written for the content stage: each update site, the last time it answered (Taipei, a YYYY-MM-DD first), and what
tonight did with it. Written whole (a .new renamed), last of the night. Made-up sites only."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from nknu.archive import update
from nknu.archive.frontier import Frontier
from tests.archive.test_update_stage2_1116 import SCRIPT

NIGHT = "2026-11-17T01:00:05+08:00"


def _status(root: Path, **over) -> None:
    (root / "reports").mkdir(parents=True, exist_ok=True)
    status = {"night_started": NIGHT, "failed_step": None, "sites_changed": ["a.nknu.edu.tw", "b.nknu.edu.tw"],
              "alarms": {"b.nknu.edu.tw": "维护页"},
              "sites_seen": {"a.nknu.edu.tw": "2026-11-16T17:10:00+00:00", "b.nknu.edu.tw": "2026-11-16T17:20:00+00:00",
                             "c.nknu.edu.tw": "2026-11-16T17:30:00+00:00", "d.nknu.edu.tw": "2026-11-14T17:00:00+00:00",
                             "e.nknu.edu.tw": None}, **over}
    (root / "reports" / update.STATUS).write_text(json.dumps(status, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def root(tmp_path):
    Frontier(tmp_path / "frontier.db").close()
    _status(tmp_path)
    return tmp_path


def test_the_sites_to_export_are_those_that_changed_and_no_alarmed_one(root, capsys):
    assert update.main(["--dir", str(root), "export-list"]) == 0
    assert capsys.readouterr().out.split() == ["a.nknu.edu.tw"]


def test_seen_says_each_site_its_last_answer_and_what_tonight_did(root):
    results = root / "results.tsv"
    results.write_text("a.nknu.edu.tw\tok\n", encoding="utf-8")
    out = root / "seen.tsv"
    assert update.main(["--dir", str(root), "seen", "--results", str(results), "--out", str(out)]) == 0
    rows = {line.split("\t")[0]: line.split("\t")[1:] for line in out.read_text(encoding="utf-8").splitlines()}
    assert rows["a.nknu.edu.tw"] == ["2026-11-17 01:10", "导了"]  # Taipei time, a YYYY-MM-DD first
    assert rows["b.nknu.edu.tw"][1] == "警报压着"
    assert rows["c.nknu.edu.tw"][1] == "没变动"
    assert rows["d.nknu.edu.tw"][1] == "连不上"  # it last answered before tonight began
    assert rows["e.nknu.edu.tw"] == ["", "连不上"]
    assert not list(root.glob("*.new"))


def test_a_site_whose_export_failed_says_so(root):
    results = root / "results.tsv"
    results.write_text("a.nknu.edu.tw\tfailed\n", encoding="utf-8")
    out = root / "seen.tsv"
    update.main(["--dir", str(root), "seen", "--results", str(results), "--out", str(out)])
    # no earlier seen.tsv: no night is known when its export last went through (the review's F5)
    assert "a.nknu.edu.tw\t\t导出失败" in out.read_text(encoding="utf-8").splitlines()


# ---------------------------------------------------------------- the nightly script

needs_script = pytest.mark.skipif(not SCRIPT.is_file(), reason="deploy/lab-crawler/lab/ is not in this checkout")


def _fakes(tmp_path: Path, sites: str) -> tuple[Path, Path]:
    """A python that says what it was asked (its steps) and prints `sites` for export-list, and an export script that
    records its list and writes one result line per site."""
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    py = bin_ / "python"
    py.write_text("#!/usr/bin/env bash\n"
                  'step="$1"; [ "$1" = "-m" ] && step="$5"; [ "$1" = "-c" ] && step="run"; [ "$1" = "-" ] && step="backup"\n'
                  f'echo "$step $*" >> "{tmp_path}/calls.log"\n'
                  '[ "$step" = "sites" ] && echo "a.nknu.edu.tw"\n'
                  f'[ "$step" = "export-list" ] && printf "{sites}"\n'
                  "exit 0\n", encoding="utf-8")
    py.chmod(0o755)
    ex = bin_ / "export_sites.sh"
    ex.write_text("#!/usr/bin/env bash\n"
                  f'echo "export $1 NIGHTLY=$NIGHTLY" >> "{tmp_path}/calls.log"\n'
                  'while read -r s; do [ -n "$s" ] && printf "%s\\tok\\n" "$s" >> "$EXPORT_RESULTS"; done < "$1"\n',
                  encoding="utf-8")
    ex.chmod(0o755)
    return py, ex


def _run(tmp_path: Path, sites: str):
    py, ex = _fakes(tmp_path, sites)
    (tmp_path / "archive" / "warc").mkdir(parents=True)
    env = {**os.environ, "PY": str(py), "ROOT": str(tmp_path / "archive"), "BACKUPS": str(tmp_path / "b"),
           "LOCKS": str(tmp_path / "locks"), "TZ": "Asia/Taipei", "NKNU_SCHOOL_LOCK": str(tmp_path / "locks" / "s"), "JS_SITES_FILE": str(tmp_path / "no-js-sites.txt"),
           "EXPORT_SCRIPT": str(ex), "EXPORT_BASE": str(tmp_path / "sites")}
    got = subprocess.run(["bash", str(SCRIPT)], check=False, env=env, capture_output=True, text=True)
    calls = (tmp_path / "calls.log").read_text().splitlines()
    return got, [c.split()[0] for c in calls], calls


@needs_script
def test_the_night_exports_the_changed_sites_after_its_review_then_writes_seen(tmp_path):
    got, steps, calls = _run(tmp_path, "a.nknu.edu.tw\\n")
    assert got.returncode == 0, (tmp_path / "archive" / "update.log").read_text(encoding="utf-8")
    assert steps == ["warc-there", "backup", "put-back", "night", "sites", "run", "review", "export-list", "export", "seen"]
    assert "NIGHTLY=1" in next(c for c in calls if c.startswith("export "))
    seen = next(c for c in calls if c.startswith("seen "))
    assert str(tmp_path / "sites" / "seen.tsv") in seen


@needs_script
def test_a_night_with_nothing_to_export_still_writes_seen(tmp_path):
    got, steps, _calls = _run(tmp_path, "")
    assert got.returncode == 0 and steps == ["warc-there", "backup", "put-back", "night", "sites", "run", "review", "export-list", "seen"]


# ---------------------------------------------------------------- the export service at night

def test_at_night_a_site_with_rows_still_waiting_is_exported_and_its_result_recorded(tmp_path):
    """A night leaves a resting server's rows for the next night: the export service (NIGHTLY=1) exports the site
    anyway; by day such a site waits as before. Each site's result goes to EXPORT_RESULTS."""
    import shutil

    from tests.archive.test_export_1027 import SITE, _add, make_archive
    from tests.archive.test_export_review_1108 import SERVICE, _service

    if not SERVICE.exists() or shutil.which("bash") is None:
        pytest.skip("the lab tools are on the disk, not in git")
    arch = make_archive(tmp_path / "archive").open()
    _add(arch.f, url="https://c.nknu.edu.tw/aca/waiting.aspx", state="pending", status=None, fetched_at=None)
    arch.close()
    results = tmp_path / "results.tsv"
    code, said = _service(tmp_path, arch, EXPORT_RESULTS=str(results))
    assert code == 0 and "还没爬完" in said and results.read_text(encoding="utf-8") == f"{SITE}\tnot_ready\n", said
    results.unlink()
    code, said = _service(tmp_path, arch, NIGHTLY="1", EXPORT_RESULTS=str(results))
    assert code == 0 and f"好了 {SITE}" in said, said
    assert results.read_text(encoding="utf-8") == f"{SITE}\tok\n"


# ---------------------------------------------------------------- the review's fixes (F5, F6, F8)

def test_a_site_changed_on_a_night_whose_export_did_not_happen_is_exported_the_next_night(root, capsys):
    """F6: night 1 changed a and b and was killed before its export; night 2 changed nothing: both are still owed."""
    update.owe(root, ["a.nknu.edu.tw", "b.nknu.edu.tw"])  # night 1's review
    _status(root, sites_changed=[], alarms={})  # night 2's
    update.owe(root, [])
    assert update.main(["--dir", str(root), "export-list"]) == 0
    assert capsys.readouterr().out.split() == ["a.nknu.edu.tw", "b.nknu.edu.tw"]


def test_only_what_was_exported_is_no_longer_owed(root, capsys):
    update.owe(root, ["a.nknu.edu.tw", "c.nknu.edu.tw"])
    _status(root, sites_changed=["a.nknu.edu.tw", "c.nknu.edu.tw"], alarms={})
    results = root / "results.tsv"
    results.write_text("a.nknu.edu.tw\tok\nc.nknu.edu.tw\tfailed\n", encoding="utf-8")
    update.main(["--dir", str(root), "seen", "--results", str(results), "--out", str(root / "seen.tsv")])
    capsys.readouterr()
    _status(root, sites_changed=[], alarms={})  # the next night: nothing changed
    update.main(["--dir", str(root), "export-list"])
    assert capsys.readouterr().out.split() == ["c.nknu.edu.tw"]


def test_an_alarmed_or_failed_site_keeps_its_last_good_night_in_seen(root):
    """F5: the second column is the night the site was last seen whole and exported, never a night an alarm held
    it or its export failed."""
    out = root / "seen.tsv"
    out.write_text("a.nknu.edu.tw\t2026-11-15 01:20\t导了\nb.nknu.edu.tw\t2026-11-15 01:30\t导了\n", encoding="utf-8")
    results = root / "results.tsv"
    results.write_text("a.nknu.edu.tw\tfailed\n", encoding="utf-8")
    update.main(["--dir", str(root), "seen", "--results", str(results), "--out", str(out)])
    rows = {line.split("\t")[0]: line.split("\t")[1:] for line in out.read_text(encoding="utf-8").splitlines()}
    assert rows["a.nknu.edu.tw"] == ["2026-11-15 01:20", "导出失败"]
    assert rows["b.nknu.edu.tw"] == ["2026-11-15 01:30", "警报压着"]
    assert rows["c.nknu.edu.tw"][0].startswith("2026-11-17")  # seen tonight, nothing to export


def test_the_status_file_says_what_the_export_did(root):
    """F8: the night's export in the status file (the dashboard's mark reads it)."""
    results = root / "results.tsv"
    results.write_text("a.nknu.edu.tw\tok\n", encoding="utf-8")
    update.main(["--dir", str(root), "seen", "--results", str(results), "--out", str(root / "seen.tsv")])
    status = json.loads((root / "reports" / update.STATUS).read_text(encoding="utf-8"))
    assert status["export"]["exported"] == ["a.nknu.edu.tw"] and status["export"]["failed"] == []


def test_two_exports_never_work_on_one_folder_at_once(tmp_path):
    """The review's F7: an export waits for the one holding the folder's lock, then gives up (EXPORT_LOCK_WAIT)
    without touching anything. Needs the flock command (the lab's Linux; checked there 11-16, not on a Mac)."""
    import shutil

    from tests.archive.test_export_review_1108 import SERVICE

    if shutil.which("flock") is None or not SERVICE.exists():
        pytest.skip("no flock command here (the lab's containers have it)")
    base = tmp_path / "sites"
    base.mkdir()
    (tmp_path / "list.txt").write_text("x.nknu.edu.tw\n", encoding="utf-8")
    holder = subprocess.Popen(["flock", str(base / ".export.lock"), "sleep", "10"])
    try:
        got = subprocess.run(["bash", str(SERVICE), str(tmp_path / "list.txt")], capture_output=True, text=True,
                             env={**os.environ, "EXPORT_BASE": str(base), "EXPORT_LOCK_WAIT": "1"}, check=False)
    finally:
        holder.kill()
    assert got.returncode == 1 and "另一个导出在用" in got.stdout + got.stderr
    assert not (base / "ready.tsv").exists()
