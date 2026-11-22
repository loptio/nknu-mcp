"""11-23, the WARC folder moves to another disk: Docker makes an empty folder for a mount whose path is wrong, and the
night's own check (the folder is there and can be written) passes on it; the night would then store new answers in the
empty folder and read none of the old ones back. `update warc-there` asks the ledger which WARC files its stored
answers are in and looks for each under the archive."""
import os
import subprocess
from pathlib import Path

import pytest

from nknu.archive import update
from nknu.archive.frontier import Frontier

from .test_update_stage2_1116 import SCRIPT, _fake_python

DAYS = ("warc/2026/11/04/crawl-1.warc.gz", "warc/2026/11/20/crawl-31.warc.gz", "warc/2026/11/22/crawl-40.warc.gz")


def _archive(tmp_path: Path, stored: tuple[str, ...] = DAYS, on_disk: tuple[str, ...] = DAYS) -> Path:
    """An archive whose ledger has one stored page in each WARC file of stored, with the files of on_disk there."""
    root = tmp_path / "archive"
    (root / "warc").mkdir(parents=True)
    f = Frontier(root / "frontier.db")
    for i, warc in enumerate(stored):
        f.conn.execute("INSERT INTO url(url, path, host, site, tier, kind, depth, found_via, state, status, warc, warc_offset) "
                       "VALUES (?, '/', 'a.nknu.edu.tw', 'a.nknu.edu.tw', 1, 'page', 0, 'seed', 'done', 200, ?, 0)",
                       (f"https://a.nknu.edu.tw/{i}", warc))
    f.conn.execute("INSERT INTO url(url, path, host, site, tier, kind, depth, found_via) "
                   "VALUES ('https://a.nknu.edu.tw/new', '/new', 'a.nknu.edu.tw', 'a.nknu.edu.tw', 1, 'page', 1, 'link')")
    f.close()
    for warc in on_disk:
        (root / warc).parent.mkdir(parents=True, exist_ok=True)
        (root / warc).write_bytes(b"x")
    return root


def test_every_file_the_ledger_names_is_there(tmp_path, capsys):
    root = _archive(tmp_path)
    assert update.main(["--dir", str(root), "warc-there"]) == 0
    assert "3 个" in capsys.readouterr().out


def test_an_empty_folder_is_the_wrong_folder(tmp_path, capsys):
    """What a mount with a wrong path gives: the folder is there, can be written, and holds nothing."""
    root = _archive(tmp_path, on_disk=())
    assert update.main(["--dir", str(root), "warc-there"]) == 1
    said = capsys.readouterr().out
    assert DAYS[-1] in said and "3 个" in said


def test_a_copy_that_misses_the_last_night_is_the_wrong_folder(tmp_path, capsys):
    """The folder was copied, a night ran on the old one, then the mount was changed: the newest file is not there."""
    root = _archive(tmp_path, on_disk=DAYS[:2])
    assert update.main(["--dir", str(root), "warc-there"]) == 1
    assert DAYS[-1] in capsys.readouterr().out


def test_an_older_file_missing_is_said_apart_from_the_wrong_folder(tmp_path, capsys):
    """The newest is there, so this is the folder the crawler writes in; a hole in it is told, and is another code."""
    root = _archive(tmp_path, on_disk=(DAYS[0], DAYS[2]))
    assert update.main(["--dir", str(root), "warc-there"]) == 2
    said = capsys.readouterr().out
    assert DAYS[1] in said and "1 个" in said


def test_a_ledger_with_nothing_stored_yet_passes(tmp_path):
    root = _archive(tmp_path, stored=(), on_disk=())
    assert update.main(["--dir", str(root), "warc-there"]) == 0


def test_the_answers_of_presses_and_the_kept_versions_count_too(tmp_path, capsys):
    """A WARC file may hold only button presses' answers, or only answers a later night replaced."""
    root = _archive(tmp_path, stored=DAYS[:1], on_disk=DAYS[:1])
    f = Frontier(root / "frontier.db")
    f.conn.execute("INSERT INTO action(list_id, epoch, step, kind, queue, host, site, profile, form_src_warc, form_src_offset, "
                   "target, row_key, state, warc, warc_offset) VALUES (1, 0, 0, 'press', 'q', 'a.nknu.edu.tw', "
                   "'a.nknu.edu.tw', 'p', ?, 0, 't', 'k', 'done', ?, 0)", (DAYS[0], DAYS[1]))
    f.conn.execute("INSERT INTO url_version(url_id, kept_at, warc, warc_offset) VALUES (1, 0, ?, 0)", (DAYS[2],))
    f.close()
    assert update.main(["--dir", str(root), "warc-there"]) == 1  # DAYS[2], the newest, is named by a kept version only
    said = capsys.readouterr().out
    assert DAYS[2] in said and "2 个" in said


# ---------------------------------------------------------------- the nightly script asks before anything


needs_script = pytest.mark.skipif(not SCRIPT.is_file(), reason="deploy/lab-crawler/lab/ is not in this checkout")


def _night(tmp_path: Path, answer: int) -> subprocess.CompletedProcess:
    """The nightly script with a python whose `update warc-there` ends with answer."""
    py = _fake_python(tmp_path, None)
    text = py.read_text(encoding="utf-8")
    py.write_text(text.replace("exit 0\n", f'[ "$step" = "warc-there" ] && {{ echo "原档的话"; exit {answer}; }}\nexit 0\n'),
                  encoding="utf-8")
    (tmp_path / "archive" / "warc").mkdir(parents=True)
    env = {**os.environ, "PY": str(py), "ROOT": str(tmp_path / "archive"), "BACKUPS": str(tmp_path / "b"),
           "LOCKS": str(tmp_path / "locks"), "TZ": "Asia/Taipei",
           "NKNU_SCHOOL_LOCK": str(tmp_path / "locks" / "school-requests.lock"),
           "JS_SITES_FILE": str(tmp_path / "no-js-sites.txt")}
    return subprocess.run(["bash", str(SCRIPT)], check=False, env=env, capture_output=True, text=True)


def _steps(tmp_path: Path) -> list[str]:
    return [line.split()[0] for line in (tmp_path / "calls.log").read_text().splitlines()]


@needs_script
def test_the_night_stops_before_anything_in_the_wrong_warc_folder(tmp_path):
    got = _night(tmp_path, 1)
    assert got.returncode == 1 and _steps(tmp_path) == ["warc-there", "fail"]  # nothing backed up, nothing put back
    log = (tmp_path / "archive" / "update.log").read_text(encoding="utf-8")
    assert "环境不对" in log and "原档的话" in log and "停在：preflight" in log


@needs_script
def test_the_night_goes_on_and_says_so_when_only_an_older_file_is_missing(tmp_path):
    got = _night(tmp_path, 2)
    assert got.returncode == 0 and _steps(tmp_path)[:3] == ["warc-there", "backup", "put-back"]
    assert "原档的话" in (tmp_path / "archive" / "update.log").read_text(encoding="utf-8")
