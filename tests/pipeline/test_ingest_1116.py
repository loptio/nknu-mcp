"""The nightly ingest (lab_pc/nightly_ingest.py; spec docs/superpowers/specs/2026-11-16-nightly-ingest-design.md, the
owner 11-16 「照这样做」): the night's steps on a made-up repository, with libraries, lanes and a build that are
stand-ins. No school data, no model, no container."""

from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import pytest

KIT = Path(__file__).resolve().parents[2] / "deploy" / "lab-pipeline"  # the programs (in the repository since 11-19)
P = "research/pipeline_2026-11-02"
ACA, CHAIR, NEW_SITE = "c.nknu.edu.tw/aca", "sso.nknu.edu.tw/site/207016", "c.nknu.edu.tw/new"


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


bh = _load("build_helpers", KIT / "build_helpers.py")
ni = _load("nightly_ingest", KIT / "nightly_ingest.py")


def export(repo: Path, folder: str, site: str, ready: bool = True) -> str:
    d = repo / "archive-export" / "sites" / folder
    d.mkdir(parents=True)
    if ready:
        (d / "manifest.json").write_text(json.dumps({"网站": site, "名称": site, "项目": []}, ensure_ascii=False),
                                         encoding="utf-8")
    return f"archive-export/sites/{folder}"


def make_library(path: Path, built_at: str, sites: dict[str, tuple[int, str]]) -> None:
    """A stand-in library: sites {site: (documents, export folder)}; one passage per document."""
    tmp = path.with_name(path.name + ".making")
    tmp.unlink(missing_ok=True)
    conn = sqlite3.connect(tmp)
    conn.executescript("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);"
                       "CREATE TABLE docs (doc_id TEXT PRIMARY KEY, site TEXT);"
                       "CREATE TABLE chunks (chunk_id INTEGER PRIMARY KEY, doc_id TEXT);"
                       "CREATE TABLE sites (site TEXT PRIMARY KEY, docs INTEGER, export TEXT);")
    conn.execute("INSERT INTO meta VALUES ('built_at', ?)", (built_at,))
    n = 0
    for site, (count, folder) in sites.items():
        conn.execute("INSERT INTO sites VALUES (?, ?, ?)", (site, count, folder))
        for _ in range(count):
            n += 1
            conn.execute("INSERT INTO docs VALUES (?, ?)", (f"d{n}", site))
            conn.execute("INSERT INTO chunks (doc_id) VALUES (?)", (f"d{n}",))
    conn.commit()
    conn.close()
    os.replace(tmp, path)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """Two listed sites, each with an old export the library in use was built from; the crawler has since exported
    教务处 again, exports a site the list lacks, and is half-way through another folder of the second site."""
    root = tmp_path / "work"
    (root / P / "lab_pc").mkdir(parents=True)
    a_old, c_old = export(root, "aca-1115", ACA), export(root, "chair-1115", CHAIR)
    a_new = export(root, "aca-1118", ACA)
    export(root, "new-1118", NEW_SITE)
    (root / P / "exports.txt").write_text(f"# 清单\n{a_old}\n\n{c_old}\n", encoding="utf-8")
    (root / "archive-export" / "sites" / "current.tsv").write_text(
        f"{ACA}\t/work/{a_new}\n{CHAIR}\t/work/{c_old}\n{NEW_SITE}\t/work/archive-export/sites/new-1118\n", encoding="utf-8")
    paths = ni.Paths(root)
    make_library(paths.db, "2026-11-16T14:56:00+08:00", {ACA: (40, a_old), CHAIR: (30, c_old)})
    monkeypatch.setattr(ni, "FLAG", tmp_path / "out" / "ingest-swapped.flag")
    (tmp_path / "out").mkdir()
    return paths


class Calls:
    """Stand-ins for the lanes and the build, counting their calls."""

    def __init__(self, paths, lanes: str = "ok", rc: int = 0, built: dict | None = None, held: dict | None = None):
        self.paths, self.lanes_result, self.rc, self.built, self.held = paths, lanes, rc, built, held
        self.lanes_calls = self.build_calls = 0

    def lanes(self, paths, limit):
        self.lanes_calls += 1
        return self.lanes_result

    def build(self, paths, limit: float = 0.0, options=()):  # run_build's own order: a word in limit's place is caught
        self.build_calls += 1
        assert isinstance(limit, (int, float)), limit
        self.build_options = tuple(options)
        if self.built is not None:  # the build's swap: a new file under the library's name
            make_library(paths.db, "2026-11-18T03:10:00+08:00", self.built)
        if self.held is not None:
            make_library(paths.held_db, "2026-11-18T03:10:00+08:00", self.held)
        return self.rc


def night(paths, calls, left_of=lambda paths: {}) -> int:
    return ni.night(paths, lanes=calls.lanes, build=calls.build, left_of=left_of)


def status(paths) -> dict:
    return json.loads(paths.status.read_text(encoding="utf-8"))


def listed(path: Path) -> list[str]:
    return [x for x in path.read_text(encoding="utf-8").splitlines() if x and not x.startswith("#")]


NEW = {ACA: (44, "archive-export/sites/aca-1118"), CHAIR: (30, "archive-export/sites/chair-1115")}


def test_a_run_after_midnight_is_the_night_befores():
    def at(text: str) -> str:
        return ni.night_of(datetime.fromisoformat(text))

    assert at("2026-11-19T02:00:00+08:00") == "2026-11-18" and at("2026-11-18T18:55:00+08:00") == "2026-11-18"
    assert at("2026-11-19T11:59:59+08:00") == "2026-11-18" and at("2026-11-19T12:00:00+08:00") == "2026-11-19"
    assert at("2026-11-18T18:00:00+00:00") == "2026-11-18"  # 02:00 the next day in Taipei, whatever zone it is said in


# ---------------------------------------------------------------- plan and repoint

def test_the_plan_moves_only_listed_sites_with_a_finished_newer_folder(repo):
    export(repo.repo, "chair-1118", CHAIR, ready=False)  # being written: no manifest yet
    with (repo.current).open("a", encoding="utf-8") as f:
        f.write(f"{CHAIR}\t/work/archive-export/sites/chair-1118\n")
    got = ni.plan(repo)
    assert got.moved == [ni.Move(ACA, "archive-export/sites/aca-1115", "archive-export/sites/aca-1118")]
    assert got.unlisted == [NEW_SITE] and got.not_ready == [CHAIR]


def test_a_held_site_stays_at_its_folder_and_the_night_says_so(repo, capsys):
    repo.hold.write_text(f"# 导出有问题，先留在原来的资料夹\n{ACA}\t等爬虫修好\n", encoding="utf-8")
    got = ni.plan(repo)
    assert got.moved == [] and got.held == [ACA]
    calls = Calls(repo, built=NEW)
    assert night(repo, calls) == 0 and (calls.lanes_calls, calls.build_calls) == (0, 0)  # nothing else is new
    s = status(repo)
    assert (s["outcome"], s["sites_held"], s["sites_new"]) == ("nothing_new", [ACA], [])
    assert listed(repo.exports)[0] == "archive-export/sites/aca-1115"
    ni.foresee(repo)
    assert "留在原来资料夹的 1 站" in capsys.readouterr().out
    repo.hold.unlink()
    assert [m.site for m in ni.plan(repo).moved] == [ACA]


def test_repointing_changes_that_line_alone_and_keeps_the_list_as_it_was(repo):
    ni.repoint(repo, ni.plan(repo).moved)
    assert repo.exports.read_text(encoding="utf-8") == (
        "# 清单\narchive-export/sites/aca-1118\n\narchive-export/sites/chair-1115\n")
    assert listed(repo.exports.with_name("exports.txt.bak-night"))[0] == "archive-export/sites/aca-1115"


def test_a_list_that_would_not_hold_is_not_written(repo):
    wrong = export(repo.repo, "other-1118", NEW_SITE + "2")  # a folder of another site
    before = repo.exports.read_text(encoding="utf-8")
    with pytest.raises(bh.ExportsError):
        ni.repoint(repo, [ni.Move(ACA, "archive-export/sites/aca-1115", wrong)])
    assert repo.exports.read_text(encoding="utf-8") == before
    assert not repo.exports.with_name("exports.txt.new-night").exists()


# ---------------------------------------------------------------- the night

def test_a_night_with_new_folders_converts_builds_and_swaps(repo):
    repo.checks.write_text(json.dumps({"built_at": "2026-11-18T03:10:00+08:00", "new": [[ACA, "same_title"]],
                                       "sites": {ACA: {"flags": ["same_title"], "same_title": 23}}}), encoding="utf-8")
    calls = Calls(repo, built=NEW)
    assert night(repo, calls) == 0 and (calls.lanes_calls, calls.build_calls) == (1, 1)
    s = status(repo)
    assert (s["outcome"], s["step"], s["error"]) == ("swapped", "done", None)
    assert s["night"] == ni.night_of(datetime.fromisoformat(s["started_at"])) and s["finished_at"] >= s["started_at"]
    assert s["sites_new"] == [{"site": ACA, "folder": "archive-export/sites/aca-1118"}]
    assert s["sites_unlisted"] == [NEW_SITE] and s["sites_not_ready"] == []
    assert s["library"]["before"] == {"docs": 70, "chunks": 70, "built_at": "2026-11-16T14:56:00+08:00", "sites": {ACA: 40}}
    assert s["library"]["after"] == {"docs": 74, "chunks": 74, "built_at": "2026-11-18T03:10:00+08:00", "sites": {ACA: 44}}
    assert s["new_reminders"] == [{"site": ACA, "kind": "same_title", "count": 23}] and s["held"] is None
    # the list, the library kept beside, and the two lists that protect the folders
    assert listed(repo.exports) == ["archive-export/sites/aca-1118", "archive-export/sites/chair-1115"]
    assert ni.library(repo.prev_db)["built_at"] == "2026-11-16T14:56:00+08:00"
    assert listed(repo.official) == ["archive-export/sites/aca-1118", "archive-export/sites/chair-1115"]
    assert listed(repo.prev_list) == ["archive-export/sites/aca-1115", "archive-export/sites/chair-1115"]
    assert ni.FLAG.read_text(encoding="utf-8").strip() == "2026-11-18T03:10:00+08:00"
    history = [json.loads(x) for x in repo.history.read_text(encoding="utf-8").splitlines()]
    assert [h["outcome"] for h in history] == ["swapped"]


def test_a_night_with_nothing_new_builds_nothing(repo):
    repo.current.write_text(f"{ACA}\t/work/archive-export/sites/aca-1115\n", encoding="utf-8")
    calls = Calls(repo, built=NEW)
    assert night(repo, calls) == 0 and (calls.lanes_calls, calls.build_calls) == (0, 0)
    assert status(repo)["outcome"] == "nothing_new" and not ni.FLAG.exists()
    # the folders of the library in use are named in a list of their own from the first night on
    assert listed(repo.official) == ["archive-export/sites/aca-1115", "archive-export/sites/chair-1115"]


def test_a_held_swap_leaves_the_library_and_the_next_night_builds_again(repo):
    held = {ACA: (3, "archive-export/sites/aca-1118"), CHAIR: (30, "archive-export/sites/chair-1115")}
    calls = Calls(repo, rc=3, held=held)
    assert night(repo, calls) == 3
    s = status(repo)
    assert s["outcome"] == "held" and s["held"] == {
        "reason": "site_collapsed", "reasons": ["site_collapsed"], "detail": [{"site": ACA, "before": 40, "after": 3}],
        "db": f"{P}/aca.held.db"}
    assert ni.library(repo.db)["built_at"] == "2026-11-16T14:56:00+08:00" and not ni.FLAG.exists()
    assert listed(repo.official)[0] == "archive-export/sites/aca-1115"  # the library in use still needs its folders
    assert listed(repo.exports)[0] == "archive-export/sites/aca-1118"
    # nothing newer the next night, but the library is behind the list: it is built again
    again = Calls(repo, built=NEW)
    assert night(repo, again) == 0 and again.build_calls == 1 and status(repo)["outcome"] == "swapped"
    assert status(repo)["sites_new"] == []


# ------------------------------------------------- what left the library, and why (spec 2026-11-17-ingest-guard §二)

LEFT = {"checked": True, "left": 20, "explained": {"merged": 2}, "unexplained": {"no_file": 18},
        "unexplained_total": 18, "by_site": {ACA: 18},
        "items": [{"site": ACA, "title": f"第{i}號表.pdf", "url": f"https://example.invalid/{i}.pdf", "why": "no_file",
                   "detail": "连接失败｜读取逾时"} for i in range(18)],
        "limits": {"total": 30, "site": 15}, "words": {"merged": "并进另一份", "no_file": "导出那一行没有档了"}}


def told(paths, **more) -> None:
    """The file nknu.content.build leaves beside the library at every swap it decides."""
    paths.swap_json.write_text(json.dumps({"swapped": True, "held": None, "forced": False, "collapsed": [],
                                           "too_many_left": False, "departures": LEFT, **more}, ensure_ascii=False),
                               encoding="utf-8")


class Told(Calls):
    def __init__(self, paths, swap: dict, **kw):
        super().__init__(paths, **kw)
        self.swap = swap

    def build(self, paths, limit: float = 0.0, options=()):
        told(paths, **self.swap)
        return super().build(paths, limit, options)


def test_too_many_documents_leaving_without_a_reason_is_a_held_night_that_says_which(repo, capsys):
    held = {ACA: (26, "archive-export/sites/aca-1118"), CHAIR: (30, "archive-export/sites/chair-1115")}
    calls = Told(repo, {"swapped": False, "held": "aca.held.db", "too_many_left": True}, rc=3, held=held)
    assert night(repo, calls) == 3
    s = status(repo)
    assert s["outcome"] == "held" and s["held"] == {
        "reason": "unexplained_departures", "reasons": ["unexplained_departures"], "detail": [], "db": f"{P}/aca.held.db"}
    assert s["departures"] == LEFT and s["unexplained_left"] == 18 and s["library"]["after"] is None
    assert not ni.FLAG.exists()
    assert json.loads(repo.history.read_text(encoding="utf-8").splitlines()[-1])["departures"]["unexplained_total"] == 18
    capsys.readouterr()
    assert ni.show(repo) == 0
    said = capsys.readouterr().out
    assert "把关没过" in said and "说不出原因地离开库" in said and "--force-swap" in said
    assert "换库会让 20 份文件离开库，说得出原因的：并进另一份 2" in said
    assert "说不出原因的 18 份（导出那一行没有档了 18）" in said and "超过全库 30 份或一站 15 份就不换库" in said
    assert said.count("｜导出那一行没有档了（连接失败｜读取逾时）") == 8 and "其余的在" in said


def test_both_guards_at_once_are_both_said(repo):
    held = {ACA: (3, "archive-export/sites/aca-1118"), CHAIR: (30, "archive-export/sites/chair-1115")}
    calls = Told(repo, {"swapped": False, "held": "aca.held.db", "too_many_left": True,
                        "collapsed": [[ACA, 40, 3]]}, rc=3, held=held)
    assert night(repo, calls) == 3
    assert status(repo)["held"] == {"reason": "site_collapsed", "reasons": ["site_collapsed", "unexplained_departures"],
                                    "detail": [{"site": ACA, "before": 40, "after": 3}], "db": f"{P}/aca.held.db"}


def test_a_wave_of_school_deletions_is_a_held_night_of_its_own_reason(repo, capsys):
    wave = {**LEFT, "explained": {"school_deleted": 70}, "unexplained": {}, "unexplained_total": 0, "by_site": {},
            "items": [], "left": 70, "waves": {ACA: [70, 600]}, "words": {"school_deleted": "学校已删除"}}
    held = {ACA: (26, "archive-export/sites/aca-1118"), CHAIR: (30, "archive-export/sites/chair-1115")}
    calls = Told(repo, {"swapped": False, "held": "aca.held.db", "school_wave": True, "departures": wave}, rc=3, held=held)
    assert night(repo, calls) == 3
    assert status(repo)["held"]["reasons"] == ["school_wave"] and status(repo)["held"]["reason"] == "school_wave"
    capsys.readouterr()
    ni.show(repo)
    said = capsys.readouterr().out
    assert "超过一成的文件被标成学校那边删掉了" in said and f"{ACA} 70/600" in said and "说不出原因的：0 份" in said


def test_a_swapped_night_keeps_what_left_too(repo, capsys):
    few = {**LEFT, "unexplained": {}, "unexplained_total": 0, "by_site": {}, "items": [], "left": 2}
    assert night(repo, Told(repo, {"departures": few}, built=NEW)) == 0
    s = status(repo)
    assert s["outcome"] == "swapped" and s["departures"] == few and s["held"] is None and s["unexplained_left"] == 0
    capsys.readouterr()
    ni.show(repo)
    said = capsys.readouterr().out
    assert "换库会让 2 份文件离开库，说得出原因的：并进另一份 2" in said and "说不出原因的：0 份" in said
    assert "要人看" not in said.splitlines()[0]


def test_a_swap_with_a_few_unexplained_says_so_on_its_first_line(repo, capsys):
    few = {**LEFT, "unexplained": {"no_text": 6}, "unexplained_total": 6, "by_site": {ACA: 6}, "items": LEFT["items"][:6]}
    assert night(repo, Told(repo, {"departures": few}, built=NEW)) == 0  # under the limits: swapped all the same
    assert status(repo)["outcome"] == "swapped" and status(repo)["unexplained_left"] == 6
    capsys.readouterr()
    ni.show(repo)
    first = capsys.readouterr().out.splitlines()[0]
    assert "换上了新库" in first and f"有 6 份文件说不出原因地离开库或被掏空（{ACA} 6），要人看" in first


def test_an_earlier_builds_file_is_not_this_nights(repo):
    told(repo)
    long_ago = repo.swap_json.stat().st_mtime - 3600
    os.utime(repo.swap_json, (long_ago, long_ago))
    assert night(repo, Calls(repo, built=NEW)) == 0
    assert status(repo)["outcome"] == "swapped" and status(repo)["departures"] is None
    assert status(repo)["unexplained_left"] is None
    repo.swap_json.write_text("not json", encoding="utf-8")
    assert ni.read_swap(repo, 0) is None and ni._left_words(None) == [] and ni._left_words({"checked": False}) == []


def test_lanes_not_through_in_time_defer_the_build(repo):
    calls = Calls(repo, lanes="timeout", built=NEW)
    assert night(repo, calls) == 0 and calls.build_calls == 0
    assert (status(repo)["outcome"], status(repo)["error"], status(repo)["step"]) == ("deferred", "lanes_timeout", "convert")


# ---------------------------------------------------------------- 11-19: slow files wait a night, the rest is built

@pytest.fixture
def two_new(repo):
    """Both listed sites have a newer export tonight; the second one's still holds files the lanes did not reach."""
    c_new = export(repo.repo, "chair-1118", CHAIR)
    cur = repo.current.read_text(encoding="utf-8").replace("/work/archive-export/sites/chair-1115", f"/work/{c_new}")
    repo.current.write_text(cur, encoding="utf-8")
    return repo


def slow_chair(paths) -> dict:
    """The stand-in for nightly_ingest.waiting: 講座's files wait while its line names the new folder."""
    return {CHAIR: 3} if any("chair-1118" in line for line in listed(paths.exports)) else {}


def test_sites_still_converting_wait_a_night_and_the_others_are_built(two_new, capsys):
    built = {ACA: (44, "archive-export/sites/aca-1118"), CHAIR: (30, "archive-export/sites/chair-1115")}
    calls = Calls(two_new, lanes="timeout", built=built)
    assert night(two_new, calls, slow_chair) == 0 and calls.build_calls == 1
    assert calls.build_options == ("--skip-marker-check",)  # the lanes were stopped by the night itself
    # the list the build read: 教务处 at its new folder, 講座 back at the one the library in use was built from
    assert listed(two_new.exports) == ["archive-export/sites/aca-1118", "archive-export/sites/chair-1115"]
    s = status(two_new)
    assert (s["outcome"], s["error"]) == ("swapped", None)
    assert s["sites_waiting"] == [{"site": CHAIR, "files": 3}] and [x["site"] for x in s["sites_new"]] == [ACA]
    ni.show(two_new)
    said = capsys.readouterr().out
    assert "换上了新库" in said and "这一晚没换新资料的网站" in said and f"{CHAIR}（还有 3 个档）" in said
    # the next night moves 講座 on again by itself
    assert [m.site for m in ni.plan(two_new).moved] == [CHAIR]


def test_only_slow_sites_new_is_a_deferred_night(two_new):
    # 教务处 is not new tonight: once 講座 went back the list is the library's, and there is nothing to build
    cur = two_new.current.read_text(encoding="utf-8").replace("aca-1118", "aca-1115")
    two_new.current.write_text(cur, encoding="utf-8")
    calls = Calls(two_new, lanes="timeout")
    assert night(two_new, calls, slow_chair) == 0 and calls.build_calls == 0
    s = status(two_new)
    assert (s["outcome"], s["error"]) == ("deferred", "lanes_timeout") and s["sites_waiting"] == [{"site": CHAIR, "files": 3}]
    assert listed(two_new.exports) == ["archive-export/sites/aca-1115", "archive-export/sites/chair-1115"]


def test_a_slow_site_with_nothing_to_go_back_to_defers_the_night(two_new):
    # the library in use does not hold 講座 (a site listed since): its files cannot wait at an older folder
    make_library(two_new.db, "2026-11-16T14:56:00+08:00", {ACA: (40, "archive-export/sites/aca-1115")})
    calls = Calls(two_new, lanes="timeout", built=NEW)
    assert night(two_new, calls, slow_chair) == 0 and calls.build_calls == 0
    s = status(two_new)
    assert (s["outcome"], s["error"]) == ("deferred", "lanes_timeout") and "sites_waiting" not in s
    assert listed(two_new.exports) == ["archive-export/sites/aca-1118", "archive-export/sites/chair-1118"]  # untouched


def test_the_real_build_takes_its_words_after_the_script(two_new, monkeypatch):
    # 11-19 morning: the options went into run_build's limit and the first real run broke at the build; the real
    # function, with a stand-in for the process, gets the option as a word of build.sh and the limit as a number
    seen = {}

    class Done:
        def __init__(self, cmd, cwd=None, **more):
            seen["cmd"] = cmd

        def wait(self, timeout=None):
            seen["timeout"] = timeout
            return 0

    monkeypatch.setattr(ni.subprocess, "Popen", Done)
    calls = Calls(two_new, lanes="timeout")
    assert ni.night(two_new, lanes=calls.lanes, build=ni.run_build, left_of=slow_chair, gpu=lambda: None,
                    disk=lambda: 100.0) in (0, 1)
    assert seen["cmd"][-1] == "--skip-marker-check" and seen["cmd"][1].endswith("build.sh")
    assert seen["timeout"] == ni.BUILD_LIMIT


def test_going_back_needs_another_folder_than_the_one_listed(repo):
    lib = ni.library(repo.db)
    assert not ni.go_back(repo, lib, {CHAIR: 2})  # 講座 is at the library's folder already and still waits
    assert not ni.go_back(repo, lib, {NEW_SITE: 1})  # not listed, not in the library
    assert listed(repo.exports) == ["archive-export/sites/aca-1115", "archive-export/sites/chair-1115"]


# ---------------------------------------------------------------- 11-19: a held build's fault stays with its sites

def blame(site: str, n: int = 20, **more) -> dict:
    """A held build's swap file whose guard blames site alone: n documents left it without a reason."""
    left = {**LEFT, "left": n, "unexplained": {"no_file": n}, "unexplained_total": n, "by_site": {site: n}}
    return {"swapped": False, "held": "aca.held.db", "too_many_left": True, "departures": left, **more}


class Twice(Calls):
    """A first build the guards hold (its swap file as given), then builds that swap."""

    def __init__(self, paths, first: dict, built: dict):
        super().__init__(paths)
        self.first, self.then = first, built

    def build(self, paths, limit: float = 0.0, options=()):
        self.build_calls += 1
        self.lists = [*getattr(self, "lists", []), listed(paths.exports)]
        if self.build_calls == 1:
            told(paths, **self.first)
            make_library(paths.held_db, "2026-11-18T03:10:00+08:00", self.then)
            return 3
        told(paths)
        make_library(paths.db, "2026-11-18T03:40:00+08:00", self.then)
        return 0


def test_the_site_the_guards_blame_goes_back_and_the_rest_is_built_again(two_new, capsys):
    built = {ACA: (44, "archive-export/sites/aca-1118"), CHAIR: (30, "archive-export/sites/chair-1115")}
    calls = Twice(two_new, blame(CHAIR), built)
    assert night(two_new, calls) == 0 and calls.build_calls == 2
    # the second build read 講座 at the folder the library in use was built from
    assert calls.lists[0] == ["archive-export/sites/aca-1118", "archive-export/sites/chair-1118"]
    assert calls.lists[1] == ["archive-export/sites/aca-1118", "archive-export/sites/chair-1115"]
    s = status(two_new)
    assert (s["outcome"], s["error"]) == ("swapped", None)
    assert s["sites_set_back"] == [{"site": CHAIR, "why": "unexplained_departures", "count": 20,
                                    "folder": "archive-export/sites/chair-1118"}]
    assert s["set_back_from"]["reasons"] == ["unexplained_departures"] and [x["site"] for x in s["sites_new"]] == [ACA]
    assert not two_new.held_db.exists()  # the first try's library is gone; why it was held is in the status file
    assert ni.read_refused(two_new.refused)[CHAIR]["folder"] == "archive-export/sites/chair-1118"
    ni.show(two_new)
    said = capsys.readouterr().out
    assert "把关退回、这一晚没换新资料的网站" in said and "说不出原因离开的文件太多，20 份" in said and "ingest-refused.txt" in said
    # the next night does not try the refused folder again, and says so; a newer export of the site is tried
    todo = ni.plan(two_new)
    assert todo.moved == [] and todo.refused == [CHAIR]
    newer = export(two_new.repo, "chair-1119", CHAIR)
    two_new.current.write_text(two_new.current.read_text(encoding="utf-8").replace("chair-1118", "chair-1119"), encoding="utf-8")
    todo = ni.plan(two_new)
    assert [(m.site, m.new) for m in todo.moved] == [(CHAIR, newer)] and todo.refused == []


def test_a_person_may_have_a_refused_folder_tried_again(two_new):
    calls = Twice(two_new, blame(CHAIR), {ACA: (44, "archive-export/sites/aca-1118"),
                                           CHAIR: (30, "archive-export/sites/chair-1115")})
    assert night(two_new, calls) == 0
    lines = [x for x in two_new.refused.read_text(encoding="utf-8").splitlines() if not x.startswith(CHAIR)]
    two_new.refused.write_text("\n".join(lines) + "\n", encoding="utf-8")  # the line deleted by hand
    assert [m.site for m in ni.plan(two_new).moved] == [CHAIR]


def test_when_the_blamed_site_was_all_that_was_new_the_night_is_held(two_new):
    cur = two_new.current.read_text(encoding="utf-8").replace("aca-1118", "aca-1115")
    two_new.current.write_text(cur, encoding="utf-8")
    calls = Twice(two_new, blame(CHAIR), {})
    assert night(two_new, calls) == 3 and calls.build_calls == 1
    s = status(two_new)
    assert s["outcome"] == "held" and s["sites_set_back"][0]["site"] == CHAIR and s["unexplained_left"] == 20
    assert s["held"]["reasons"] == ["unexplained_departures"] and two_new.held_db.exists()  # as a held night was
    assert listed(two_new.exports) == ["archive-export/sites/aca-1115", "archive-export/sites/chair-1115"]
    assert ni.plan(two_new).refused == [CHAIR]


@pytest.mark.parametrize("first", [
    # spread thin: no site over its own limit, the whole library's limit passed
    {"swapped": False, "held": "aca.held.db", "too_many_left": True,
     "departures": {**LEFT, "unexplained_total": 36, "by_site": {f"s{i}.example": 12 for i in range(3)}}},
    # one site over its limit, and the others together over the library's limit all the same
    {"swapped": False, "held": "aca.held.db", "too_many_left": True,
     "departures": {**LEFT, "unexplained_total": 60, "by_site": {CHAIR: 20, **{f"s{i}.example": 10 for i in range(4)}}}},
    # more sites at fault than SET_BACK_MAX
    {"swapped": False, "held": "aca.held.db", "too_many_left": False,
     "collapsed": [[f"s{i}.example", 40, 3] for i in range(6)]},
])
def test_a_trouble_that_is_no_sites_alone_is_held_as_before(two_new, first):
    assert ni.culprits(first) == {}
    calls = Twice(two_new, first, {})
    assert night(two_new, calls) == 3 and calls.build_calls == 1
    s = status(two_new)
    assert s["outcome"] == "held" and "sites_set_back" not in s and two_new.held_db.exists()
    assert listed(two_new.exports) == ["archive-export/sites/aca-1118", "archive-export/sites/chair-1118"]
    assert ni.read_refused(two_new.refused) == {}


def test_who_is_blamed():
    assert ni.SET_BACK_MAX == 5
    assert ni.culprits(None) == {} and ni.culprits({"swapped": True, "held": None}) == {}
    got = ni.culprits({"swapped": False, "held": "aca.held.db", "collapsed": [[ACA, 40, 3]], "too_many_left": False})
    assert got == {ACA: {"why": "site_collapsed", "count": 37}}
    got = ni.culprits(blame(CHAIR, school_wave=True) | {"departures": {
        **LEFT, "unexplained_total": 20, "by_site": {CHAIR: 20}, "waves": {ACA: [60, 400]}}})
    assert got == {CHAIR: {"why": "unexplained_departures", "count": 20}, ACA: {"why": "school_wave", "count": 60}}
    assert ni.culprits(blame("")) == {}  # a site without a name is no site to set back


def test_a_blamed_site_with_nothing_to_go_back_to_is_held_and_not_marked_refused(two_new):
    make_library(two_new.db, "2026-11-16T14:56:00+08:00", {ACA: (40, "archive-export/sites/aca-1115")})
    calls = Twice(two_new, blame(CHAIR), {})
    assert night(two_new, calls) == 3 and calls.build_calls == 1
    assert status(two_new)["outcome"] == "held" and ni.read_refused(two_new.refused) == {}
    assert listed(two_new.exports) == ["archive-export/sites/aca-1118", "archive-export/sites/chair-1118"]


def test_a_lane_that_fails_ends_the_night_there(repo):
    calls = Calls(repo, lanes="lane_A_exit_2", built=NEW)
    assert night(repo, calls) == 1 and calls.build_calls == 0
    assert (status(repo)["outcome"], status(repo)["error"]) == ("failed", "lane_A_exit_2")


def test_paused_the_lanes_convert_and_nothing_is_built(repo):
    repo.pause.write_text("看过再删\n", encoding="utf-8")
    calls = Calls(repo, built=NEW)
    assert night(repo, calls) == 0 and (calls.lanes_calls, calls.build_calls) == (1, 0)
    assert status(repo)["outcome"] == "paused" and not repo.prev_db.exists()


def test_a_build_that_fails_before_the_swap_is_a_failed_night(repo):
    calls = Calls(repo, rc=2)
    assert night(repo, calls) == 1
    assert (status(repo)["outcome"], status(repo)["error"], status(repo)["step"]) == ("failed", "build_exit_2", "build")
    assert ni.library(repo.db)["built_at"] == "2026-11-16T14:56:00+08:00"


def test_checks_that_fail_after_the_swap_do_not_undo_it(repo):
    calls = Calls(repo, rc=7, built=NEW)
    assert night(repo, calls) == 0
    assert (status(repo)["outcome"], status(repo)["error"]) == ("swapped", "checks_exit_7")


def test_a_list_that_cannot_be_read_is_a_failed_night_with_its_reason(repo):
    repo.exports.write_text("archive-export/sites/not-there\n", encoding="utf-8")
    calls = Calls(repo, built=NEW)
    assert night(repo, calls) == 1 and calls.lanes_calls == 0
    s = status(repo)
    assert s["outcome"] == "failed" and s["error"].startswith("ExportsError") and s["finished_at"]


def test_too_little_disk_ends_the_night_before_anything_is_changed(repo, capsys):
    before = repo.exports.read_text(encoding="utf-8")
    calls = Calls(repo, built=NEW)
    assert ni.night(repo, lanes=calls.lanes, build=calls.build, disk=lambda: 9.4) == 1
    assert (calls.lanes_calls, calls.build_calls) == (0, 0) and repo.exports.read_text(encoding="utf-8") == before
    s = status(repo)
    assert (s["outcome"], s["error"], s["disk_free_gb"]) == ("failed", "disk_low_9GB", 9.4)
    ni.show(repo)
    assert "C 盘只剩约 9 GB" in capsys.readouterr().out


def test_disk_that_runs_low_while_converting_stops_before_the_build(repo):
    free = iter([40.0, 12.0])
    calls = Calls(repo, built=NEW)
    assert ni.night(repo, lanes=calls.lanes, build=calls.build, disk=lambda: next(free)) == 1
    assert (calls.lanes_calls, calls.build_calls) == (1, 0) and not repo.prev_db.exists()
    assert status(repo)["error"] == "disk_low_12GB"


def test_the_card_and_the_disk_are_noted_and_a_build_past_its_limit_is_a_failed_night(repo):
    calls = Calls(repo, rc=ni.EXIT_TIMEOUT)
    assert ni.night(repo, lanes=calls.lanes, build=calls.build, disk=lambda: 34.1, gpu=lambda: [19303, 20475]) == 1
    s = status(repo)
    assert (s["outcome"], s["error"], s["gpu_mb"], s["disk_free_gb"]) == ("failed", "build_timeout", [19303, 20475], 34.1)
    swapped = Calls(repo, rc=ni.EXIT_TIMEOUT, built=NEW)  # the checks after the swap ran out of time: the swap stands
    assert ni.night(repo, lanes=swapped.lanes, build=swapped.build, disk=lambda: 34.1, gpu=lambda: None) == 0
    assert (status(repo)["outcome"], status(repo)["error"]) == ("swapped", "checks_timeout")


def test_a_build_that_does_not_end_is_stopped(repo, tmp_path, monkeypatch):
    monkeypatch.setattr(ni, "PROGRAMS", repo.kit)  # a stand-in build.sh, where this test can write one
    (repo.kit / "build.sh").write_text("sleep 30\n", encoding="utf-8")
    assert ni.run_build(repo, limit=0.5) == ni.EXIT_TIMEOUT
    (repo.kit / "build.sh").write_text("exit 7\n", encoding="utf-8")
    assert ni.run_build(repo, limit=5) == 7


def test_one_night_at_a_time(repo):
    with ni.one_at_a_time(repo) as mine:
        assert mine
        calls = Calls(repo, built=NEW)
        assert night(repo, calls) == 0 and (calls.lanes_calls, calls.build_calls) == (0, 0)
    assert not repo.status.exists()
    (line,) = [json.loads(x) for x in repo.history.read_text(encoding="utf-8").splitlines()]
    assert line["outcome"] == "busy" and line["night"] == ni.night_of(datetime.fromisoformat(line["started_at"]))


# ---------------------------------------------------------------- the lanes

class Proc:
    def __init__(self, ends_at: float | None, code: int = 0):
        self.ends_at, self.code, self.returncode, self.signals, self.killed = ends_at, code, None, [], False

    def poll(self):
        if self.returncode is None and self.ends_at is not None and CLOCK[0] >= self.ends_at:
            self.returncode = self.code
        return self.returncode

    def send_signal(self, sig):
        self.signals.append(sig)
        self.ends_at = CLOCK[0] + 10  # it puts its file back and ends

    def kill(self):
        self.killed, self.returncode = True, -9


CLOCK = [0.0]


def lanes_with(repo, procs: dict, limit: float = 100) -> str:
    CLOCK[0] = 0.0
    started = []

    def start(cmd, **kw):
        started.append(cmd)
        return procs[cmd[-1]]

    def sleep(seconds):
        CLOCK[0] += seconds

    got = ni.run_lanes(repo, limit, start=start, clock=lambda: CLOCK[0], sleep=sleep)
    # the lanes' script is the one beside the program (deploy/lab-pipeline since 11-19), not the state folder's
    assert [c[-1] for c in started] == list(ni.LANES) and all(c[1] == str(ni.PROGRAMS / "lanes.sh") for c in started)
    assert ni.PROGRAMS.name == "lab-pipeline" and (ni.PROGRAMS / "build.sh").is_file()
    return got


def test_the_lanes_run_side_by_side_until_each_has_ended(repo):
    assert lanes_with(repo, {"A": Proc(30), "C": Proc(5), "B": Proc(60)}) == "ok"
    assert lanes_with(repo, {"A": Proc(30), "C": Proc(5, code=2), "B": Proc(60)}) == "lane_C_exit_2"


def test_past_the_limit_the_lanes_are_asked_to_stop(repo):
    procs = {"A": Proc(None), "C": Proc(5), "B": Proc(60)}
    assert lanes_with(repo, procs, limit=100) == "timeout"
    assert len(procs["A"].signals) == 1 and not procs["A"].killed and procs["C"].signals == []


# ---------------------------------------------------------------- rollback, status in words

def test_rollback_puts_the_library_of_before_back_and_pauses(repo, capsys):
    assert ni.rollback(repo) == 1  # nothing kept beside the library yet
    assert night(repo, Calls(repo, built=NEW)) == 0
    ni.FLAG.unlink()
    assert ni.rollback(repo) == 0
    assert ni.library(repo.db)["built_at"] == "2026-11-16T14:56:00+08:00"
    assert ni.library(repo.db.with_name("aca.rolled-back.db"))["built_at"] == "2026-11-18T03:10:00+08:00"
    assert repo.pause.exists() and ni.FLAG.exists()
    assert listed(repo.official)[0] == "archive-export/sites/aca-1115"
    back = json.loads(repo.history.read_text(encoding="utf-8").splitlines()[-1])
    assert (back["outcome"], back["night"]) == ("rolled_back", ni.night_of(datetime.fromisoformat(back["started_at"])))
    assert back["finished_at"] >= back["started_at"] and back["db"] == f"{P}/aca.rolled-back.db"
    assert back["library"]["after"]["built_at"] == "2026-11-16T14:56:00+08:00"  # the library it went back to
    assert back["library"]["before"]["built_at"] == "2026-11-18T03:10:00+08:00"
    assert ni.rollback(repo) == 1  # the library in use is the kept one now
    # paused: the next night converts and builds nothing, though the library is behind the list
    calls = Calls(repo, built=NEW)
    assert night(repo, calls) == 0 and calls.build_calls == 0 and status(repo)["outcome"] == "paused"
    assert "暂停" in capsys.readouterr().out


def test_last_night_in_words(repo, capsys):
    assert ni.show(repo) == 0 and "还没有" in capsys.readouterr().out
    night(repo, Calls(repo, built=NEW))
    capsys.readouterr()
    assert ni.show(repo) == 0
    out = capsys.readouterr().out
    assert "换上了新库" in out and ACA in out and "70 → 74" in out and NEW_SITE in out


def test_what_a_night_would_do_is_said_and_nothing_is_written(repo, capsys):
    before = sorted(x.name for x in repo.pipe.iterdir())
    assert ni.foresee(repo) == 0
    out = capsys.readouterr().out
    assert "有新资料夹的 1 站" in out and ACA in out and NEW_SITE in out and "会转文字、重建、把关、换库" in out
    assert "就是照现在的清单建的" in out and sorted(x.name for x in repo.pipe.iterdir()) == before
    repo.current.write_text("", encoding="utf-8")
    ni.foresee(repo)
    assert "今晚没有新资料，不重建" in capsys.readouterr().out


def test_a_run_by_hand_may_give_the_lanes_longer(repo, monkeypatch):
    seen = []
    monkeypatch.setattr(ni, "night", lambda paths, limit=ni.LANE_LIMIT: seen.append(limit) or 0)
    monkeypatch.setenv("NKNU_LANE_WORK", str(repo.repo))
    assert ni.main(["run"]) == 0
    monkeypatch.setenv("NKNU_LANE_LIMIT", "21600")
    assert ni.main(["run"]) == 0
    assert seen == [ni.LANE_LIMIT, 21600.0]


# ---- promote: a trial that passed its checks takes the library's place without a rebuild (session 爬虫judge for the
# owner, 11-18). The trial is as build.sh --db leaves it: NAME.db, NAME.swap.json and its reports in NAME/.

TRIAL, TRIAL_AT = "sites_1118a", "2026-11-18T14:10:00+08:00"


def make_trial(paths, swap: dict | None = None, built: str = TRIAL_AT) -> Path:
    db = ni.trial_db(paths, TRIAL)
    make_library(db, built, NEW)
    if swap is not None:
        db.with_name(f"{TRIAL}.swap.json").write_text(json.dumps(
            {"swapped": True, "held": None, "forced": False, "collapsed": [], "too_many_left": False,
             "departures": LEFT, **swap}, ensure_ascii=False), encoding="utf-8")
    folder = paths.pipe / TRIAL
    (folder / "totext").mkdir(parents=True)
    (folder / "build_report.json").write_text('{"of": "trial"}', encoding="utf-8")
    (folder / "totext" / "summary.json").write_text('{"of": "trial"}', encoding="utf-8")
    (folder / "site_checks.json").write_text(json.dumps(
        {"built_at": built, "sites": {ACA: {"same_title": 3}}, "new": [[ACA, "same_title"]]}), encoding="utf-8")
    return db


@pytest.fixture
def listed_new(repo):
    """The list already names 教务处's newer folder (a paused run converted it), and the build before left reports."""
    repo.exports.write_text("archive-export/sites/aca-1118\narchive-export/sites/chair-1115\n", encoding="utf-8")
    (repo.pipe / "build_report.json").write_text('{"of": "before"}', encoding="utf-8")
    (repo.pipe / "checks.md").write_text("before\n", encoding="utf-8")
    return repo


def test_a_checked_trial_takes_the_librarys_place_without_a_rebuild(listed_new, capsys):
    repo, db = listed_new, make_trial(listed_new, {})
    assert ni.promote(repo, TRIAL) == 0
    assert repo.db.samefile(db) and ni.library(repo.db)["built_at"] == TRIAL_AT
    assert ni.library(repo.prev_db)["built_at"] == "2026-11-16T14:56:00+08:00"  # rollback has what to go back to
    assert listed(repo.official)[0] == "archive-export/sites/aca-1118"
    assert listed(repo.prev_list)[0] == "archive-export/sites/aca-1115"
    s = status(repo)
    assert (s["outcome"], s["step"], s["error"], s["promoted"]) == ("swapped", "done", None, f"{P}/{TRIAL}.db")
    assert s["sites_new"] == [{"site": ACA, "folder": "archive-export/sites/aca-1118"}]
    assert (s["library"]["before"]["docs"], s["library"]["after"]["docs"]) == (70, 74)
    assert s["departures"] == LEFT and s["unexplained_left"] == 18
    assert s["new_reminders"] == [{"site": ACA, "kind": "same_title", "count": 3}]
    assert json.loads(repo.history.read_text(encoding="utf-8").splitlines()[-1])["outcome"] == "swapped"
    assert ni.FLAG.exists() and json.loads(repo.swap_json.read_text(encoding="utf-8"))["swapped"] is True
    # the trial's reports stand beside the library; the ones of the build before are kept as build.sh keeps them
    assert json.loads((repo.pipe / "build_report.json").read_text(encoding="utf-8")) == {"of": "trial"}
    assert (repo.pipe / "totext" / "summary.json").is_file() and (repo.pipe / "checks.md").read_text() == "before\n"
    kept = [q for q in (repo.kit / "previous").iterdir()]
    assert len(kept) == 1 and json.loads((kept[0] / "build_report.json").read_text(encoding="utf-8")) == {"of": "before"}
    assert sorted(s["reports"]) == ["build_report.json", "site_checks.json", "totext/summary.json"]
    ni.show(repo)
    out = capsys.readouterr().out
    assert "换上了新库" in out and "没有重建" in out and "就是照现在的清单建的" not in out
    assert ni.foresee(repo) == 0 and "就是照现在的清单建的" in capsys.readouterr().out
    assert ni.promote(repo, TRIAL) == 1  # it is the library now
    ni.FLAG.unlink()
    assert ni.rollback(repo) == 0 and ni.library(repo.db)["built_at"] == "2026-11-16T14:56:00+08:00"


@pytest.mark.parametrize("case", ["never_built", "no_swap_file", "held", "forced", "not_compared", "older", "name"])
def test_a_trial_that_was_not_checked_against_the_library_is_refused(listed_new, case, capsys):
    repo, name = listed_new, TRIAL
    if case == "never_built":  # still the hard link the trial's name began as
        os.link(repo.db, ni.trial_db(repo, TRIAL))
    elif case == "no_swap_file":
        make_trial(repo, None)
    elif case == "held":
        make_trial(repo, {"swapped": False, "held": f"{TRIAL}.held.db", "too_many_left": True})
    elif case == "forced":
        make_trial(repo, {"forced": True})
    elif case == "not_compared":
        make_trial(repo, {"departures": {"checked": False}})
    elif case == "older":
        make_trial(repo, {}, built="2026-11-15T06:43:00+08:00")
    else:
        make_trial(repo, {})
        name = "aca.prev"
    assert ni.promote(repo, name) == 1
    assert "不换" in capsys.readouterr().err
    assert ni.library(repo.db)["built_at"] == "2026-11-16T14:56:00+08:00"
    assert not repo.prev_db.exists() and not repo.status.exists() and not ni.FLAG.exists()
    assert json.loads((repo.pipe / "build_report.json").read_text(encoding="utf-8")) == {"of": "before"}


def test_what_breaks_after_the_swap_is_said_and_the_swap_stands(listed_new, monkeypatch):
    repo, db = listed_new, make_trial(listed_new, {})

    def broken(paths, name):
        raise OSError("no room")

    monkeypatch.setattr(ni, "take_reports", broken)
    assert ni.promote(repo, TRIAL) == 0
    s = status(repo)
    assert repo.db.samefile(db) and s["outcome"] == "swapped" and s["error"].startswith("after_swap_OSError")
    assert ni.library(repo.prev_db)["built_at"] == "2026-11-16T14:56:00+08:00" and ni.FLAG.exists()


def test_the_promoted_trials_later_checks_follow_it(listed_new, capsys):
    repo = listed_new
    make_trial(repo, {})
    assert ni.reports(repo, TRIAL) == 1  # not the library yet
    assert ni.promote(repo, TRIAL) == 0
    (repo.pipe / TRIAL / "checks.md").write_text("of the trial\n", encoding="utf-8")
    (repo.pipe / TRIAL / "offline_new_r3.json").write_text("{}", encoding="utf-8")
    capsys.readouterr()
    assert ni.reports(repo, TRIAL) == 0
    assert "checks.md" in capsys.readouterr().out and (repo.pipe / "checks.md").read_text() == "of the trial\n"
    assert (repo.pipe / "offline_new_r3.json").is_file()
    assert any((q / "checks.md").is_file() and (q / "checks.md").read_text() == "before\n"
               for q in (repo.kit / "previous").iterdir())
    assert ni.reports(repo, TRIAL) == 0 and "没有新的" in capsys.readouterr().out
    make_library(repo.db, "2026-11-19T02:10:00+08:00", NEW)  # a night built again: the trial is no longer the library
    assert ni.reports(repo, TRIAL) == 1


def test_promote_and_reports_from_the_command_line(listed_new, monkeypatch):
    monkeypatch.setenv("NKNU_LANE_WORK", str(listed_new.repo))
    make_trial(listed_new, {})
    assert ni.main(["promote"]) == 2
    assert ni.main(["promote", TRIAL]) == 0 and ni.main(["reports", TRIAL]) == 0


# ---------------------------------------------------------------- 11-22: build.sh step 5b, the question sample


class Sampled(Calls):
    """build.sh since 11-22: past its swap, step 5b writes the question sample's run record (whatever happened) and,
    when it measured, the measurement."""

    def __init__(self, paths, rc_5b: int | None = 0, **kw):
        super().__init__(paths, **kw)
        self.rc_5b = rc_5b

    def build(self, paths, limit: float = 0.0, options=()):
        rc = super().build(paths, limit, options)
        if self.rc_5b is not None:
            (paths.pipe / ni.STUDENT_EVAL_RUN).write_text(
                json.dumps({"exit": self.rc_5b, "seconds": 241, "at": "2026-11-23T03:30:00+08:00"}), encoding="utf-8")
        if self.rc_5b == 0:
            (paths.pipe / ni.STUDENT_EVAL).write_text(json.dumps({
                "measured": "2026-11-23T03:30:00", "library": {"built": "2026-11-18T03:10:00+08:00"}, "n": 73,
                "totals": {"B": {"raw": [20, 30, 38]}}, "questions": [{"id": "B001", "raw": 1}]}), encoding="utf-8")
        return rc


def test_a_swapped_night_says_what_the_question_sample_did(repo):
    assert night(repo, Sampled(repo, built=NEW)) == 0
    said = status(repo)["student_eval"]
    assert said == {"outcome": "ok", "exit": 0, "seconds": 241, "measured": "2026-11-23T03:30:00",
                    "library_built": "2026-11-18T03:10:00+08:00", "n": 73, "totals": {"B": {"raw": [20, 30, 38]}}}
    assert "questions" not in json.dumps(status(repo))  # totals only: no question is ever in the status


@pytest.mark.parametrize(("rc", "outcome"), [(124, "timeout"), (4, "failed")])
def test_a_sample_that_failed_is_only_said_and_the_swap_stands(repo, rc, outcome):
    assert night(repo, Sampled(repo, rc_5b=rc, built=NEW)) == 0
    assert status(repo)["outcome"] == "swapped"
    assert status(repo)["student_eval"] == {"outcome": outcome, "exit": rc, "seconds": 241}


def test_no_sample_this_night_is_none_and_an_old_record_is_not_this_nights(repo):
    assert night(repo, Sampled(repo, rc_5b=None, built=NEW)) == 0
    assert status(repo)["student_eval"] is None
    run = repo.pipe / ni.STUDENT_EVAL_RUN
    run.write_text('{"exit": 0, "seconds": 1}', encoding="utf-8")
    long_ago = run.stat().st_mtime - 3600
    os.utime(run, (long_ago, long_ago))
    assert ni.student_eval_ran(repo, run.stat().st_mtime + 3600) is None
    run.write_text("not json", encoding="utf-8")
    assert ni.student_eval_ran(repo, 0) == {"outcome": "unreadable"}


def test_a_promoted_trial_brings_its_own_question_sample(listed_new):
    # the trial's build.sh wrote step 5b into the trial's folder; promote says it, and the measurement moves along
    repo = listed_new
    make_trial(repo, {})
    t = repo.pipe / TRIAL
    (t / ni.STUDENT_EVAL_RUN).write_text('{"exit": 0, "seconds": 226, "at": "x"}', encoding="utf-8")
    (t / ni.STUDENT_EVAL).write_text(json.dumps({"measured": "m", "library": {"built": TRIAL_AT}, "n": 43,
                                                 "totals": {"raw": [16, 25, 35]}}), encoding="utf-8")
    assert ni.promote(repo, TRIAL) == 0
    said = status(repo)["student_eval"]
    assert (said["outcome"], said["seconds"], said["library_built"], said["n"]) == ("ok", 226, TRIAL_AT, 43)
    assert (repo.pipe / ni.STUDENT_EVAL).is_file() and ni.STUDENT_EVAL in status(repo)["reports"]


def test_a_promoted_trial_without_a_sample_says_none(listed_new):
    make_trial(listed_new, {})
    assert ni.promote(listed_new, TRIAL) == 0 and status(listed_new)["student_eval"] is None
