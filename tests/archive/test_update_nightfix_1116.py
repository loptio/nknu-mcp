"""The fixes after the first real night (11-16, c.nknu.edu.tw/ch; the owner: 「先修这三件」; the check
wf_9c6e1448-2cc) that are still there: a list page whose rows tell nothing (a teacher's page, a list of blank rows)
still says it changed; the nightly script runs as one pinned command. (The third, a run reconciling no site under
periodic update, went with the run's own reconciliation in the judgment-layer cut of 11-19.) Made-up pages."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from nknu.archive import crawler as mod
from nknu.archive import progress, update, watch
from nknu.archive.viewer import home_html

from . import test_progress
from .test_progress import NOW, snap_of, visible
from .test_update_rotations_1116 import _row
from .test_update_select_1116 import _walk
from .test_update_stage2_1116 import SCRIPT, World, _answer, _fake_python

arch = test_progress.arch  # the dashboard's small archive (its fixture, shared)


# ---------------------------------------------------------------- the nightly script (fix 3)


needs_script = pytest.mark.skipif(not SCRIPT.is_file(), reason="deploy/lab-crawler/lab/ is not in this checkout")


def _env(tmp_path: Path, py: Path, **over) -> dict:
    """What the pinned command gives the script (compose.yaml's nightly service), each to be left out by a test."""
    (tmp_path / "archive" / "warc").mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "PY": str(py), "ROOT": str(tmp_path / "archive"), "BACKUPS": str(tmp_path / "b"),
           "LOCKS": str(tmp_path / "locks"), "TZ": "Asia/Taipei",
           "NKNU_SCHOOL_LOCK": str(tmp_path / "locks" / "school-requests.lock"), "JS_SITES_FILE": str(tmp_path / "no-js-sites.txt"), **over}
    return {k: v for k, v in env.items() if v is not None}


def _steps(tmp_path: Path) -> list[str]:
    calls = tmp_path / "calls.log"
    return [line.split()[0] for line in calls.read_text().splitlines()] if calls.exists() else []


@needs_script
@pytest.mark.parametrize("left_out", ["TZ", "NKNU_SCHOOL_LOCK", "warc"])
def test_a_command_that_left_a_setting_out_stops_before_anything(tmp_path, left_out):
    """11-16: the first night ran from a hand-typed command; one setting missing and the night goes wrong quietly (a
    UTC clock in the backup names, a lock no other program shares, a WARC folder it cannot write)."""
    py = _fake_python(tmp_path, None)
    over = {"TZ": "UTC"} if left_out == "TZ" else {"NKNU_SCHOOL_LOCK": None} if left_out == "NKNU_SCHOOL_LOCK" else {}
    env = _env(tmp_path, py, **over)
    if left_out == "warc":
        (tmp_path / "archive" / "warc").rmdir()
    got = subprocess.run(["bash", str(SCRIPT)], check=False, env=env, capture_output=True, text=True)
    assert got.returncode == 1 and _steps(tmp_path) == ["fail"]  # nothing backed up, nothing put back
    assert "环境不对" in (tmp_path / "archive" / "update.log").read_text(encoding="utf-8")


@needs_script
def test_the_status_file_says_where_the_night_stopped_even_when_python_cannot(tmp_path):
    """A command with no PYTHONPATH: `update fail` cannot run either; the script writes the status file itself."""
    py = _fake_python(tmp_path, "fail")
    got = subprocess.run(["bash", str(SCRIPT)], check=False, env=_env(tmp_path, py, TZ="UTC"), capture_output=True,
                         text=True)
    status = json.loads((tmp_path / "archive" / "reports" / "update-status.json").read_text(encoding="utf-8"))
    assert got.returncode == 1 and status["failed_step"] == "preflight" and status["failed_at"]


@needs_script
def test_each_step_is_logged_with_its_time(tmp_path):
    py = _fake_python(tmp_path, None)
    got = subprocess.run(["bash", str(SCRIPT)], check=False, env=_env(tmp_path, py), capture_output=True, text=True)
    log = (tmp_path / "archive" / "update.log").read_text(encoding="utf-8")
    assert got.returncode == 0
    for said in ("备份好了", "放回好了", "run 结束", "变动清单算好了", "定期更新结束"):
        assert said in log


@needs_script
def test_a_night_ends_when_only_resting_servers_are_left(tmp_path):
    """The first whole-school night (11-16): elecnas stopped connecting; a server that answered this archive before is
    only given up after CONN_GIVE_UP_ANSWERED tries (about a day), each rest shorter than `archive run`'s own end wait
    (1800 s), so the night would have run on into the next day and the next night would not have started. The night's
    run ends once nothing comes due sooner than a server's rest; those rows wait for the next night (run_end
    "waiting", which the review counts as a whole night)."""
    py = _fake_python(tmp_path, None)
    got = subprocess.run(["bash", str(SCRIPT)], check=False, env=_env(tmp_path, py), capture_output=True, text=True)
    assert got.returncode == 0
    run_line = next(line for line in (tmp_path / "calls.log").read_text().splitlines() if line.startswith("run "))
    words = run_line.split()
    assert "--end-wait" in words, run_line
    wait = float(words[words.index("--end-wait") + 1])
    assert 0 < wait < min(mod.CONN_PAUSE_S, mod.SERVER_REST_S)
    assert "waiting" in update.RUN_OK


@needs_script
def test_nightly_backups_are_named_apart_and_only_they_are_pruned(tmp_path):
    """BACKUPS pointed at D:\\nknu-crawl\\backup would have pruned the hand-made backup frontier-…-before-night-1116.db
    (the glob was frontier-*.db): the night's are nightly-*.db, and only those are pruned."""
    real = tmp_path / "realpy"
    real.write_text(f"#!/usr/bin/env bash\nexec {sys.executable} \"$@\"\n", encoding="utf-8")
    real.chmod(0o755)
    py = _fake_python(tmp_path, None)
    text = py.read_text()
    py.write_text(text.replace('exit 0\n', f'[ "$step" = "backup" ] && exec {real} "$@"\nexit 0\n'))
    (tmp_path / "archive").mkdir()
    db = sqlite3.connect(tmp_path / "archive" / "frontier.db")
    db.execute("CREATE TABLE url(id INTEGER PRIMARY KEY)")
    db.commit()
    db.close()
    backups = tmp_path / "b"
    backups.mkdir()
    hand = backups / "frontier-20261116-144742-before-night-1116.db"
    hand.write_bytes(b"x")
    for day in range(1, 9):
        (backups / f"nightly-202611{day:02d}-010000.db").write_bytes(b"x")
    got = subprocess.run(["bash", str(SCRIPT)], check=False, env=_env(tmp_path, py, KEEP="7"), capture_output=True,
                         text=True)
    left = sorted(p.name for p in backups.iterdir())
    assert got.returncode == 0, (tmp_path / "archive" / "update.log").read_text(encoding="utf-8")
    assert hand.name in left
    nightly = [n for n in left if n.startswith("nightly-")]
    assert len(nightly) == 7 and "nightly-20261101-010000.db" not in nightly
    assert not any(n.startswith("frontier-2026") and n != hand.name for n in left)


# ---------------------------------------------------------------- the dashboard's mark (fix 3: a missed night says so)


def _night(f, root: Path, *, ago: float | None, **status) -> None:
    f.meta(mod.UPDATE_SITES_KEY, json.dumps(["c.nknu.edu.tw/aca"]))
    if ago is not None:
        f.meta(update.NIGHT_KEY, repr(NOW - ago))
        if "counts" in status:  # a night whose review was written
            f.meta(update.REVIEWED_KEY, repr(NOW - ago))
    if status:
        (root / "reports").mkdir(exist_ok=True)
        (root / "reports" / update.STATUS).write_text(json.dumps(status, ensure_ascii=False), encoding="utf-8")


def test_the_dashboard_says_nothing_of_updates_before_go_live(arch):
    _f, _ids, root = arch
    snap = snap_of(root)
    assert snap["update"] is None and "定期更新" not in visible(home_html(snap))


def test_a_good_night_is_one_green_mark_with_its_counts_in_the_hover(arch):
    f, _ids, root = arch
    _night(f, root, ago=3600, failed_step=None, alarms={}, counts={"new": 4, "changed": 4, "same": 28},
           sites_changed=["c.nknu.edu.tw/aca"], export={"exported": ["c.nknu.edu.tw/aca"], "failed": [], "owed": []})
    u = snap_of(root)["update"]
    assert u["color"] == "green" and u["words"].startswith("定期更新 ")
    assert "新的 4" in u["tip"] and "有变动的网站 1 个" in u["tip"] and "导了 1 个" in u["tip"]
    assert u["words"] in visible(home_html(snap_of(root)))


@pytest.mark.parametrize(("ago", "words"), [(30 * 3600, "定期更新 30 小时没跑"), (3 * 86400 + 60, "定期更新 3 天没跑")])
def test_no_night_for_more_than_a_day_turns_yellow(arch, ago, words):
    f, _ids, root = arch
    _night(f, root, ago=ago, failed_step=None, alarms={}, counts={})
    u = snap_of(root)["update"]
    assert (u["color"], u["words"]) == ("yellow", words)


def test_a_night_that_stopped_is_red_and_says_where(arch):
    f, _ids, root = arch
    _night(f, root, ago=3600, failed_step="preflight", failed_at="2026-10-26T01:00:00+0800",
           note="update fail 也跑不起来")
    u = snap_of(root)["update"]
    assert (u["color"], u["words"]) == ("red", "定期更新没跑完") and "preflight" in u["tip"]


def test_an_alarm_is_red_and_names_the_site(arch):
    f, _ids, root = arch
    _night(f, root, ago=3600, failed_step=None, alarms={"c.nknu.edu.tw/aca": "6 of 10 pages"}, counts={})
    u = snap_of(root)["update"]
    assert (u["color"], u["words"]) == ("red", "定期更新警报 1 站") and "c.nknu.edu.tw/aca" in u["tip"]


def test_go_live_with_no_night_yet_says_so(arch):
    f, _ids, root = arch
    _night(f, root, ago=None)
    u = snap_of(root)["update"]
    assert (u["color"], u["words"]) == ("none", "定期更新还没跑过")


def test_a_night_still_running_says_so(arch):
    f, _ids, root = arch
    _night(f, root, ago=600, failed_step=None, alarms={}, counts={"same": 9})  # last night's file, tonight's start
    f.meta(update.REVIEWED_KEY, repr(NOW - 86400))
    u = snap_of(root)["update"]
    assert (u["color"], u["words"]) == ("none", "定期更新在跑")


def test_a_reviewed_night_still_exporting_says_it_runs(arch):
    """The export batch's review, F8: green only once the night's export is written in the status file."""
    f, _ids, root = arch
    _night(f, root, ago=600, failed_step=None, alarms={}, counts={"same": 9}, export=None)
    u = snap_of(root)["update"]
    assert (u["color"], u["words"]) == ("none", "定期更新在跑") and "导出" in u["tip"]


def test_a_night_whose_export_failed_for_a_site_is_red_and_names_it(arch):
    f, _ids, root = arch
    _night(f, root, ago=600, failed_step=None, alarms={}, counts={"same": 9},
           export={"exported": [], "failed": ["c.nknu.edu.tw/aca"], "owed": ["c.nknu.edu.tw/aca"]})
    u = snap_of(root)["update"]
    assert (u["color"], u["words"]) == ("red", "定期更新导出失败 1 站") and "c.nknu.edu.tw/aca" in u["tip"]


def test_the_dashboard_reads_the_keys_update_writes():
    assert (progress.UPDATE_SITES_KEY, progress.UPDATE_NIGHT_KEY, progress.UPDATE_REVIEWED_KEY,
            progress.UPDATE_STATUS) == (mod.UPDATE_SITES_KEY, update.NIGHT_KEY, update.REVIEWED_KEY, update.STATUS)


# ---------------------------------------------------------------- a list page whose rows tell nothing (fix 1)


CN = "https://c.nknu.edu.tw/x"


def _teacher_page(title: str) -> str:
    """A c.nknu teacher's page (teachercorner.aspx): the teacher's fields in .FocusMain, a one-row grid of files."""
    return (f"<html><body><div class='FocusMain'><span id='lbl_TeaName'>王OO</span><span id='lbl_TeaTitle'>{title}"
            f"</span><table id='MasterGrid'><tr><td><a href='{CN}/WccafpsFs/TeaFiles/a.pdf'>著作目錄</a></td></tr>"
            "</table></div></body></html>")


def _teacher_list(cell: str) -> str:
    """A c.nknu teacher list (teacher.aspx): a row is a photo linking the teacher's page and a cell of text."""
    return (f"<html><body><span id='lbl_Title'>師資</span><table id='MasterGrid'><tr><td><a href='{CN}/teachercorner"
            f".aspx?pn=5&amp;tid=1'><img src='{CN}/p.jpg'></a></td><td>{cell}</td></tr></table></body></html>")


def _news_list(clicks: int) -> str:
    return (f"<html><body><span id='lbl_Title'>最新消息</span><table id='MasterGrid'><tr><td>2026.11.09</td><td>"
            f"<a href='{CN}/newsdetail.aspx?nid=1'>公告一</a></td><td>{clicks}</td></tr></table></body></html>")


@pytest.fixture
def lw(tmp_path):
    w = World(tmp_path)
    yield w
    w.f.close()


def _list_night(w, url: str, before: str, after: str, row_href: str) -> str:
    uid = _answer(w, url, before)
    _walk(w.f, uid, "last_page", 1)
    _row(w.f, uid, row_href, "2026-11-01")  # its rows are read, long ago
    since = time.time()
    _answer(w, url, after)
    n = watch.night(w.f, w.root, since, now=time.time())
    return next(c.change for c in n.rows if c.url_id == uid), uid


def test_a_teachers_page_whose_fields_changed_is_changed(lw):
    """11-16: 51 teachercorner pages are lists of one file row; the teacher's own fields sit outside the grid and were
    never compared (wf_9c6e1448-2cc)."""
    url = f"{CN}/teachercorner.aspx?pn=5&tid=1"
    change, uid = _list_night(lw, url, _teacher_page("副教授"), _teacher_page("教授"),
                              f"{CN}/WccafpsFs/TeaFiles/a.pdf")
    assert change == "changed"
    assert lw.f.q("SELECT fp FROM page_watch WHERE url_id=?", (uid,))[0][0] is not None


def test_a_list_whose_rows_say_nothing_is_compared_whole(lw):
    """A teacher list's rows hold no title, date or category (a photo link and a cell): its whole text is compared."""
    url = f"{CN}/teacher.aspx?pn=5"
    change, _uid = _list_night(lw, url, _teacher_list("王OO 副教授 分機 3009"), _teacher_list("王OO 教授 分機 3009"),
                               f"{CN}/teachercorner.aspx?pn=5&tid=1")
    assert change == "changed"


def test_a_number_inside_a_news_lists_rows_is_no_change(lw):
    """Outside the grid only: a click count in a row (a column with no counter word next to it) never makes a list
    changed; its rows are list_row's to judge."""
    url = f"{CN}/newslist.aspx"
    change, _uid = _list_night(lw, url, _news_list(27), _news_list(28), f"{CN}/newsdetail.aspx?nid=1")
    assert change == "same"
