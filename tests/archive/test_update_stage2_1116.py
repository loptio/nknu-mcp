"""Periodic updates, stage 2 (plan docs/superpowers/plans/2026-11-16-update-stage-2.md; design 2026-11-16 §7.3-7.7):
the fingerprint of a page's own text, the night's review of what changed, the rotations, the nightly script. Made-up
pages; the list fixture is the SSO list of test_postback (its read counters)."""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from nknu.archive import crawler as crawler_mod
from nknu.archive import update, watch
from nknu.archive.frontier import Frontier
from tests.archive.test_postback import SSO_LIST, World, fx

PAGE = "https://a.nknu.edu.tw/aca/page.aspx?id=1"


def _html(body: str, extra: str = "") -> str:
    return (f"<html><head><title>頁</title><style>.x{{color:red}}</style></head><body><header>學校</header>"
            f"<nav>選單</nav><main>{body}</main><footer>頁尾 {extra}</footer><script>var n=1;</script></body></html>")


def test_a_counter_and_white_space_are_no_change():
    a = _html("<p>報名期限：11/20</p><table><tr><td>瀏覽</td><td>120</td></tr></table>")
    b = _html("<p>報名期限：  11/20 </p><table><tr><td>瀏覽</td><td>1,234</td></tr></table>")
    assert watch.fingerprint(a, PAGE) == watch.fingerprint(b, PAGE)


def test_a_deadline_moved_is_a_change():
    assert watch.fingerprint(_html("<p>報名期限：11/20</p>"), PAGE) != watch.fingerprint(_html("<p>報名期限：11/30</p>"),
                                                                                       PAGE)


def test_the_template_and_scripts_are_not_the_pages_text():
    a = _html("<p>內文</p>", extra="2026")
    b = _html("<p>內文</p>", extra="2027").replace("var n=1", "var n=2").replace("color:red", "color:blue")
    assert watch.fingerprint(a, PAGE) == watch.fingerprint(b, PAGE)


def test_a_lists_read_counters_are_no_change():
    p1 = fx("sso_list_p1.html")
    again = p1.replace(">10</", ">11</").replace(">20</", ">25</").replace(">30</", ">31</")
    assert again != p1 and watch.fingerprint(p1, SSO_LIST) == watch.fingerprint(again, SSO_LIST)
    assert watch.fingerprint(p1, SSO_LIST) != watch.fingerprint(p1.replace("停修申請時間公告", "停修申請延長"), SSO_LIST)


# ---------------------------------------------------------------- Task 2: the night's review

SITE = "a.nknu.edu.tw"
DAY = 86400.0


@pytest.fixture
def world(tmp_path):
    w = World(tmp_path)
    yield w
    w.f.close()


def _answer(w, url: str, body, *, status: int = 200, ctype: str = "text/html; charset=utf-8", kind: str = "page"):
    """An answer stored as the crawler stores one (a real WARC record), the row taken first as a fetch takes it."""
    if w.f.row(url) is None:
        w.f.set_host(SITE, queue="ip:test")
        w.f.add(url, host=SITE, site=SITE, unit=None, tier=1, kind=kind, depth=1, found_via="link", found_from=None,
                owner_site=SITE, kind0=kind, added_at=time.time())
    uid = w.f.row(url)["id"]
    w.f.activate(uid)  # its answer kept first (stage 0a)
    snap = w.archive(url, body, status, {"content-type": ctype})
    sniffed = "html" if "html" in ctype else "pdf"
    w.f.finish(uid, state="done", reason=None if status == 200 else f"http_{status}", status=status, kind=kind,
               sniff=sniffed if status == 200 else None, sha256=snap.sha256, warc=w.warc, warc_offset=snap.warc_offset,
               warc_len=snap.warc_len, final_url=url, attempts=0)
    return uid


def _night(w, since: float, now: float | None = None):
    return watch.night(w.f, w.root, since, now=time.time() if now is None else now)


def _change(night, uid):
    return next(c.change for c in night.rows if c.url_id == uid)


def test_a_page_got_tonight_for_the_first_time_is_new(world):
    since = time.time() - 1
    uid = _answer(world, f"https://{SITE}/n1.aspx", _html("<p>新公告</p>"))
    assert _change(_night(world, since), uid) == "new"


def test_a_page_read_again_is_changed_or_the_same_by_its_text(world):
    views = "<table><tr><td>瀏覽</td><td>{}</td></tr></table>"
    a = _answer(world, f"https://{SITE}/a.aspx", _html("<p>期限 11/20</p>" + views.format(10)))
    b = _answer(world, f"https://{SITE}/b.aspx", _html("<p>期限 11/20</p>"))
    since = time.time()
    _answer(world, f"https://{SITE}/a.aspx", _html("<p>期限 11/20</p>" + views.format(12)))  # a counter only
    _answer(world, f"https://{SITE}/b.aspx", _html("<p>期限 11/30</p>"))
    night = _night(world, since)
    assert (_change(night, a), _change(night, b)) == ("same", "changed")
    assert night.sites_changed == [SITE]


def test_a_page_gone_twice_a_day_apart_is_deleted_and_back_when_it_comes_back(world):
    url = f"https://{SITE}/gone.aspx"
    uid = _answer(world, url, _html("<p>公告</p>"))
    t0 = time.time()
    _answer(world, url, "<html>找不到</html>", status=404)
    assert _change(_night(world, t0, now=t0 + 10), uid) == "not_seen"
    t1 = time.time()
    _answer(world, url, "<html>找不到</html>", status=404)
    assert _change(_night(world, t1, now=t0 + DAY + 3600), uid) == "deleted"
    t2 = time.time()
    _answer(world, url, _html("<p>公告</p>"))
    assert _change(_night(world, t2, now=t0 + 2 * DAY), uid) == "back"


def test_a_file_is_changed_by_its_bytes(world):
    url = f"https://{SITE}/f.pdf"
    uid = _answer(world, url, b"%PDF-1.4 one", ctype="application/pdf", kind="file")
    since = time.time()
    _answer(world, url, b"%PDF-1.4 two", ctype="application/pdf", kind="file")
    assert _change(_night(world, since), uid) == "changed"


def test_a_site_answering_one_body_everywhere_is_an_alarm(world):
    uids = [_answer(world, f"https://{SITE}/p{i}.aspx", _html(f"<p>公告 {i}</p>")) for i in range(6)]
    since = time.time()
    for i in range(6):
        _answer(world, f"https://{SITE}/p{i}.aspx", "<html><body>系統維護中</body></html>")
    night = _night(world, since)
    assert SITE in night.alarms and SITE not in night.sites_changed
    assert {_change(night, u) for u in uids} == {"changed"}


# ---------------------------------------------------------------- Task 4: review, the status file, the nightly script

SCRIPT = Path(__file__).resolve().parents[2] / "deploy" / "lab-crawler" / "lab" / "nightly_update.sh"


def test_review_writes_the_change_list_and_the_status(world, capsys):
    world.f.x("INSERT INTO site_plan(site, name, round) VALUES (?, ?, '1')", (SITE, SITE))
    world.f.meta(crawler_mod.UPDATE_SITES_KEY, json.dumps([SITE]))
    a = _answer(world, f"https://{SITE}/a.aspx", _html("<p>甲</p>"))
    world.f.meta(update.NIGHT_KEY, repr(time.time()))  # put-back's: the night starts
    _answer(world, f"https://{SITE}/a.aspx", _html("<p>乙</p>"))
    new = _answer(world, f"https://{SITE}/b.aspx", _html("<p>丙</p>"))
    _ran(world)
    world.f.close()
    assert update.main(["--dir", str(world.root), "review"]) == 0
    status = json.loads((world.root / "reports" / "update-status.json").read_text(encoding="utf-8"))
    assert status["counts"] == {"changed": 1, "new": 1} and status["sites_changed"] == [SITE]
    rows = (world.root / "reports" / status["changes_file"]).read_text(encoding="utf-8").splitlines()
    assert rows[0] == "site,url,kind,change,previous_at" and len(rows) == 3
    assert {r.split(",")[3] for r in rows[1:]} == {"changed", "new"} and a != new
    assert "改了 1" in capsys.readouterr().out
    world.f = Frontier(world.root / "frontier.db")


def test_review_without_a_night_does_nothing(world, capsys):
    world.f.close()
    assert update.main(["--dir", str(world.root), "review"]) == 1
    assert "put-back" in capsys.readouterr().err
    world.f = Frontier(world.root / "frontier.db")


def _fake_python(tmp_path: Path, fail_at: str | None) -> Path:
    """A python that only says what it was asked (the script's steps), failing at one."""
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    py = bin_ / "python"
    py.write_text("#!/usr/bin/env bash\n"
                  'step="$1"; [ "$1" = "-m" ] && step="$5"; [ "$1" = "-c" ] && step="run"; [ "$1" = "-" ] && step="backup"\n'
                  '[ "$2" = "nknu.archive.jsfetch" ] && step="jsfetch"\n'
                  f'echo "$step $*" >> "{tmp_path}/calls.log"\n'
                  '[ "$step" = "sites" ] && echo "a.nknu.edu.tw"\n'
                  f'[ "$step" = "{fail_at or "-none-"}" ] && exit 1\n'
                  "exit 0\n", encoding="utf-8")
    py.chmod(0o755)
    return py


@pytest.mark.skipif(not SCRIPT.is_file(), reason="deploy/lab-crawler/lab/ is not in this checkout")
@pytest.mark.parametrize("fail_at", [None, "put-back", "run"])
def test_the_nightly_script_runs_the_steps_in_order_and_stops_at_a_failure(tmp_path, fail_at):
    py = _fake_python(tmp_path, fail_at)
    (tmp_path / "archive" / "warc").mkdir(parents=True)
    env = {**os.environ, "PY": str(py), "ROOT": str(tmp_path / "archive"), "BACKUPS": str(tmp_path / "backups"),
           "LOCKS": str(tmp_path / "locks"),
           "TZ": "Asia/Taipei", "NKNU_SCHOOL_LOCK": str(tmp_path / "locks" / "school.lock"), "JS_SITES_FILE": str(tmp_path / "no-js-sites.txt")}
    got = subprocess.run(["bash", str(SCRIPT)], check=False, env=env, capture_output=True, text=True)
    steps = [line.split()[0] for line in (tmp_path / "calls.log").read_text().splitlines()]
    want = {None: ["backup", "put-back", "night", "sites", "run", "review", "export-list", "seen"],  # export batch T7
            "put-back": ["backup", "put-back", "fail"],
            "run": ["backup", "put-back", "night", "sites", "run", "review", "fail"]}[fail_at]  # a failed run is reviewed too
    assert got.returncode == (0 if fail_at is None else 1) and steps == want


def _ran(world, reason: str = "finished") -> None:
    """Tonight's run as the crawler writes it: run_start, then run_end."""
    world.f.event("info", "run_start", json.dumps({"sites": [SITE]}))
    world.f.event("info", "run_end", json.dumps({"reason": reason, "pending": 0}))


def _main(world, *argv) -> int:
    world.f.close()
    try:
        return update.main(["--dir", str(world.root), *argv])
    finally:
        world.f = Frontier(world.root / "frontier.db")


def test_put_back_records_when_the_night_starts(world):
    world.f.meta(crawler_mod.UPDATE_SITES_KEY, json.dumps([SITE]))
    t0 = time.time()
    assert _main(world, "put-back") == 0
    assert t0 <= float(world.f.meta(update.NIGHT_KEY)) <= time.time()


def test_a_night_is_counted_once(world, capsys):
    a = _answer(world, f"https://{SITE}/a.aspx", _html("<p>甲</p>"))
    world.f.meta(update.NIGHT_KEY, repr(time.time()))
    _answer(world, f"https://{SITE}/a.aspx", _html("<p>乙</p>"))
    _ran(world)
    assert _main(world, "review") == 0 and _main(world, "review") == 0
    assert world.f.q("SELECT changes FROM page_watch WHERE url_id=?", (a,))[0]["changes"] == 1
    assert "已经算过" in capsys.readouterr().out


def test_the_status_names_the_runs_end(world):
    world.f.meta(update.NIGHT_KEY, repr(time.time() - 5))
    _ran(world)
    assert _main(world, "review") == 0
    status = json.loads((world.root / "reports" / update.STATUS).read_text(encoding="utf-8"))
    assert status["run_end"]["reason"] == "finished" and status["failed_step"] is None and status["counts"] == {}


def test_fail_says_where_the_night_stopped(world):
    assert _main(world, "fail", "--step", "put-back") == 0
    status = json.loads((world.root / "reports" / update.STATUS).read_text(encoding="utf-8"))
    assert status["failed_step"] == "put-back"


def test_sites_are_one_comma_list(world, capsys):
    assert _main(world, "sites") == 1  # no go-live yet
    world.f.meta(crawler_mod.UPDATE_SITES_KEY, json.dumps([SITE, "b.nknu.edu.tw"]))
    assert _main(world, "sites") == 0
    assert capsys.readouterr().out.strip() == f"{SITE},b.nknu.edu.tw"


def test_the_status_carries_each_sites_last_look_and_what_put_back_took(world):
    """Design §7.5's status file: when each site was last looked at, and how many of each batch went back tonight."""
    world.f.x("INSERT INTO site_plan(site, name, round) VALUES (?, ?, '1')", (SITE, SITE))
    world.f.meta(crawler_mod.UPDATE_SITES_KEY, json.dumps([SITE]))
    assert _main(world, "put-back", "--today", "2026-11-20") == 0
    _answer(world, f"https://{SITE}/a.aspx", _html("<p>甲</p>"))
    _ran(world)
    assert _main(world, "review") == 0
    status = json.loads((world.root / "reports" / update.STATUS).read_text(encoding="utf-8"))
    assert status["put_back"] == dict.fromkeys(update.BATCHES, 0)
    assert status["sites_seen"][SITE] == world.f.row(f"https://{SITE}/a.aspx")["fetched_at"]


@pytest.mark.skipif(not SCRIPT.is_file(), reason="deploy/lab-crawler/lab/ is not in this checkout")
def test_the_js_sites_get_their_round_after_the_review_and_are_exported_too(tmp_path):
    """11-17 evening (plan 2026-11-17-jsfetch-integration task 6, prepared, installed after the 11-18 disk move): with a
    list of JS sites (JS_SITES_FILE), the night opens them in the browser, takes the looks, fetches what they showed,
    opens the new pages and takes them again, after the review and before the export; their keys join the export list
    (the export's basis counts the looks: an unchanged site is skipped). A failing JS step is noted, never the night's
    end. With no such list, nothing of it (the test above)."""
    py = _fake_python(tmp_path, "js-take")
    (tmp_path / "archive" / "warc").mkdir(parents=True)
    js = tmp_path / "js_sites.txt"
    js.write_text("# the JS sites\nw3.nknu.edu.tw\nord.nknu.edu.tw\n", encoding="utf-8")
    fake_export = tmp_path / "export.sh"
    fake_export.write_text(f'#!/usr/bin/env bash\ncp "$1" "{tmp_path}/exported.txt"\n', encoding="utf-8")
    env = {**os.environ, "PY": str(py), "ROOT": str(tmp_path / "archive"), "BACKUPS": str(tmp_path / "backups"),
           "LOCKS": str(tmp_path / "locks"), "TZ": "Asia/Taipei",
           "NKNU_SCHOOL_LOCK": str(tmp_path / "locks" / "school.lock"), "JS_SITES_FILE": str(js),
           "EXPORT_SCRIPT": str(fake_export), "EXPORT_BASE": str(tmp_path / "sites")}
    got = subprocess.run(["bash", str(SCRIPT)], check=False, env=env, capture_output=True, text=True)
    steps = [line.split()[0] for line in (tmp_path / "calls.log").read_text().splitlines()]
    assert got.returncode == 0, got.stderr
    assert steps == ["backup", "put-back", "night", "sites", "run", "review", "jsfetch", "js-take", "run", "jsfetch", "js-take",
                     "export-list", "seen"]
    assert (tmp_path / "exported.txt").read_text().split() == ["ord.nknu.edu.tw", "w3.nknu.edu.tw"]
    log = (tmp_path / "archive" / "update.log").read_text(encoding="utf-8")
    assert "JS 网站结束（有一步出错" in log
