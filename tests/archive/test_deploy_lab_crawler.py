"""The crawler's lab kit (deploy/lab-crawler; 11-19: what runs every night was only on two disks, research/ being out
of git): the files the lab PC runs are in the repository and name each other rightly; the package holds the crawler's
own files and nothing of another session's; install.sh puts it over the lab's two copies of the program keeping
what it replaces, and refuses a broken package and a ledger a run holds; check_lab.sh tells whether the lab's files
are the repository's; nothing in the kit starts the old viewer or installs a copy for it (closed 11-21: the panels
read the archive themselves). Made-up trees under tmp_path; nothing here connects anywhere."""

from __future__ import annotations

import fcntl
import os
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
KIT = REPO / "deploy" / "lab-crawler"
SCRIPTS = ("pack.sh", "install.sh", "check_lab.sh", "proof_night.sh", "export_and_compare.sh", "lab/nightly_update.sh",
           "lab/export_sites.sh")
COPIES = ("code-update", "export-code")
linux_tools = pytest.mark.skipif(shutil.which("sha256sum") is None, reason="no sha256sum here (the lab's image has it)")


def compose() -> dict:
    return yaml.safe_load((KIT / "compose.yaml").read_text(encoding="utf-8"))


def test_every_script_parses_and_the_two_windows_files_are_plain_ascii():
    for name in SCRIPTS:
        got = subprocess.run(["bash", "-n", str(KIT / name)], capture_output=True, text=True, check=False)
        assert got.returncode == 0, (name, got.stderr)
    for name in ("nightly_update.cmd", "nightly_hidden.vbs"):  # cmd reads them in the console's code page
        raw = (KIT / name).read_bytes()
        raw.decode("ascii")
        # and every line ends as Windows ends it (2026-11-23: an edit that read the file as text wrote it back
        # with bare line feeds, and that copy was installed)
        assert raw.count(b"\n") == raw.count(b"\r\n") > 0, name


def test_the_schedule_s_chain_names_files_the_kit_holds():
    """Task Scheduler runs the .vbs, which runs the .cmd, which runs the compose file's nightly service, which runs
    the script of the kit's lab folder: each link names the next as the lab holds it."""
    assert r"C:\nknu-crawl\nightly_update.cmd" in (KIT / "nightly_hidden.vbs").read_text(encoding="ascii")
    cmd = (KIT / "nightly_update.cmd").read_text(encoding="ascii")
    assert r"docker compose -f C:\nknu-crawl\compose.yaml run --rm nightly" in cmd
    assert r"C:\nknu-transfer\build\nightly_ingest.cmd" in cmd  # the content session's ingest, after the crawler's
    night = compose()["services"]["nightly"]
    assert night["command"] == ["bash", "/crawl/code-update/lab/nightly_update.sh"]
    assert (KIT / "lab" / "nightly_update.sh").is_file() and (KIT / "lab" / "export_sites.sh").is_file()
    script = (KIT / "lab" / "nightly_update.sh").read_text(encoding="utf-8")
    assert "$LAB/export_sites.sh" in script and "$LAB/js_sites.txt" in script
    lines = (KIT / "lab" / "js_sites.txt").read_text(encoding="utf-8").splitlines()
    assert len([x for x in lines if x.strip() and not x.lstrip().startswith("#")]) == 5


def test_the_night_has_what_it_needs_and_nothing_else_holds_the_key():
    services = compose()["services"]
    assert sorted(services) == ["coverage", "nightly", "rejudge", "status", "stop", "warc-backup"]
    night = services["nightly"]
    assert night["image"] == "nknu-browser" and night["environment"]["PYTHONPATH"] == "/crawl/code-update/src"
    assert night["environment"]["TZ"] == "Asia/Taipei" and night["environment"]["NKNU_SCHOOL_LOCK"]
    mounts = night["volumes"]
    assert "nknu-crawl:/crawl" in mounts and "nknu-work:/work" in mounts and "E:/nknu/warc:/crawl/archive/warc" in mounts
    # the ledger's nightly backups go to the backup disk's one folder for this project (11-23), where the script looks
    assert "D:/nknu-backup/crawler-nightly:/backup-nightly" in mounts and night["environment"]["BACKUPS"] == "/backup-nightly"
    assert any(m.endswith("google.env:/run/secrets/google.env:ro") for m in mounts)
    for name in ("stop", "status", "rejudge"):  # a person's tools: the night's own program, no network, no key
        assert not any("secrets" in str(m) for m in services[name].get("volumes", [])), name
        assert services[name]["network_mode"] == "none" and services[name]["working_dir"] == "/crawl/code-update"
        assert not services[name].get("ports"), name
    assert not night.get("ports")  # nothing of the crawler's listens: the panels are deploy/lab-inspect's


def test_the_raw_files_backup_cannot_write_the_source_and_runs_after_the_ingest():
    # 2026-11-23: the raw crawl files are copied to the backup disk each night. Its service gets the source read-only
    # (so no fault of the program can write there), no network and no key; the schedule runs it last, so nothing it
    # does holds up the ingest, and the task still ends with the crawler's code.
    svc = compose()["services"]["warc-backup"]
    assert "E:/nknu/warc:/crawl/archive/warc:ro" in svc["volumes"]
    assert "D:/nknu-backup/crawler-warc:/backup-warc" in svc["volumes"] and "nknu-crawl:/crawl" in svc["volumes"]
    assert svc["network_mode"] == "none" and not any("secrets" in v for v in svc["volumes"])
    run = svc["entrypoint"]
    assert run[0] == "timeout" and "nknu.archive.warcbackup" in run and run[-2:] == ["--to", "/backup-warc"]
    cmd = (KIT / "nightly_update.cmd").read_text(encoding="ascii")
    ingest, backup = cmd.index("nightly_ingest.cmd >>"), cmd.index("run --rm warc-backup --quiet")
    assert ingest < backup and cmd.rstrip().endswith("exit /b %RC%")
    assert "nknu.archive import crawler, export, monitor, round1, schedules, update, viewer, warcbackup" in (
        KIT / "install.sh").read_text(encoding="utf-8")  # an install says so when the new program does not load


def test_the_night_s_coverage_is_written_after_the_ingest_by_a_service_of_its_own():
    # 2026-11-23: each night's count by site, for the panels. It only reads the ledger and writes one file among the
    # crawler's reports; the schedule runs it after the ingest, so nothing it does holds the night up.
    svc = compose()["services"]["coverage"]
    assert svc["volumes"] == ["nknu-crawl:/crawl"] and svc["network_mode"] == "none"
    run = svc["entrypoint"]
    assert run[0] == "timeout" and "nknu.archive.coverage" in run and run[-2:] == ["--dir", "/crawl/archive"]
    cmd = (KIT / "nightly_update.cmd").read_text(encoding="ascii")
    ingest, cover = cmd.index("nightly_ingest.cmd >>"), cmd.index("run --rm coverage --quiet")
    assert ingest < cover < cmd.index("run --rm warc-backup") and cmd.rstrip().endswith("exit /b %RC%")
    assert "schedules, update, viewer, warcbackup, coverage" in (KIT / "install.sh").read_text(encoding="utf-8")


def test_nothing_in_the_kit_starts_the_old_viewer_or_installs_a_copy_for_it():
    """11-21: the old viewer was closed and its copy of the program deleted on the lab. A line that starts the service
    or names its copy would bring the container back, or make the install and the check ask for a folder that is
    gone."""
    for name in ("compose.yaml", "install.sh", "check_lab.sh", "proof_night.sh", "README.md"):
        text = (KIT / name).read_text(encoding="utf-8")
        assert "up -d viewer" not in text and "viewer-code" not in text, name


def test_the_readme_names_only_files_the_kit_holds():
    text = (KIT / "README.md").read_text(encoding="utf-8")
    for name in ("compose.yaml", "nightly_update.cmd", "nightly_hidden.vbs", "lab/nightly_update.sh",
                 "lab/export_sites.sh", "lab/js_sites.txt", "browser_image/Dockerfile", "pack.sh", "install.sh",
                 "check_lab.sh", "proof_night.sh", "export_and_compare.sh", "compare_export.py"):
        assert name in text and (KIT / name).is_file(), name
    assert "nknu-nightly-update" in text and "01:00" in text


@pytest.fixture(scope="module")
def packed(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("packed")
    got = subprocess.run(["bash", str(KIT / "pack.sh"), str(out)], cwd=REPO, capture_output=True, text=True, check=False)
    assert got.returncode == 0, got.stderr
    return out


def test_the_small_package_holds_the_crawler_s_own_files_only(packed):
    with tarfile.open(packed / "crawler-code.tar") as tar:
        names = sorted(m.name.removeprefix("./") for m in tar.getmembers() if m.isfile())
    assert "MANIFEST.sha256" in names and "lab/nightly_update.sh" in names and "src/nknu/admin/cli.py" in names
    assert "config/archive_round1.json" in names and "src/nknu/archive/crawler.py" in names
    for n in names:
        assert n == "MANIFEST.sha256" or n.startswith(("src/nknu/archive/", "lab/", "config/")) or n == "src/nknu/admin/cli.py", n
    assert not [n for n in names if "__pycache__" in n or n.endswith(".pyc") or "/._" in n]
    assert {p.name for p in (REPO / "src/nknu/archive").glob("*.py")} == {n.rsplit("/", 1)[1] for n in names
                                                                          if n.startswith("src/nknu/archive/")}
    for name in ("install.sh", "check_lab.sh", "proof_night.sh", "export_and_compare.sh", "compare_export.py",
                 "compose.yaml", "nightly_update.cmd", "nightly_hidden.vbs", "crawler-code.tar.sha256"):
        assert (packed / name).is_file(), name


def lab(tmp_path: Path) -> Path:
    """A made-up /crawl: the two copies of the program as they are in the repository, with one file of the archive
    an older version, one the repository no longer has, and the nightly script an older one."""
    crawl = tmp_path / "crawl"
    for c in COPIES:
        shutil.copytree(REPO / "src", crawl / c / "src", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(REPO / "config", crawl / c / "config")
        with (crawl / c / "src/nknu/archive/watch.py").open("a", encoding="utf-8") as fh:
            fh.write("\n# an older version\n")
        (crawl / c / "src/nknu/archive/rehearsal.py").write_text("# gone from the repository\n", encoding="utf-8")
    (crawl / "code-update/lab").mkdir()
    (crawl / "code-update/lab/nightly_update.sh").write_text("#!/usr/bin/env bash\n# older\n", encoding="utf-8")
    (crawl / "archive").mkdir()
    return crawl


def run(script: str, crawl: Path, packed: Path, **env) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(packed / script)], capture_output=True, text=True, check=False,
                          env={**os.environ, "CRAWL": str(crawl), "IN": str(packed), "HOST": str(packed),
                               "ANY_HOUR": "1", **env})


@linux_tools
def test_install_puts_the_package_over_the_two_copies_and_keeps_what_it_replaces(tmp_path, packed):
    crawl = lab(tmp_path)
    assert run("check_lab.sh", crawl, packed).returncode == 1  # not the repository's yet
    got = run("install.sh", crawl, packed)
    assert got.returncode == 0, got.stdout + got.stderr
    for c in COPIES:
        arch = crawl / c / "src/nknu/archive"
        assert (arch / "watch.py").read_bytes() == (REPO / "src/nknu/archive/watch.py").read_bytes()
        kept = list(arch.glob("watch.py.prev-*"))
        assert len(kept) == 1 and kept[0].read_text(encoding="utf-8").endswith("# an older version\n")
        assert not (arch / "rehearsal.py").exists() and len(list(arch.glob("rehearsal.py.prev-*"))) == 1
        assert not list(arch.glob("crawler.py.prev-*"))  # a file that is the same is left alone
        assert f"{c}：换了 1 个档、新放 0 个、拿掉 1 个；程序载得进来" in got.stdout
    lab_dir = crawl / "code-update/lab"
    assert (lab_dir / "nightly_update.sh").read_bytes() == (KIT / "lab/nightly_update.sh").read_bytes()
    assert os.access(lab_dir / "nightly_update.sh", os.X_OK) and len(list(lab_dir.glob("nightly_update.sh.prev-*"))) == 1
    assert (lab_dir / "export_sites.sh").is_file() and (lab_dir / "js_sites.txt").is_file()
    same = run("check_lab.sh", crawl, packed)
    assert same.returncode == 0 and "不一样的 0 个" in same.stdout, same.stdout
    assert not (crawl / "viewer-code").exists()  # two copies: nothing is made for the old viewer


@linux_tools
def test_check_lab_names_each_file_that_is_not_the_repository_s(tmp_path, packed):
    crawl = lab(tmp_path)
    assert run("install.sh", crawl, packed).returncode == 0
    (crawl / "export-code/src/nknu/archive/export.py").write_text("# changed on the lab by hand\n", encoding="utf-8")
    (crawl / "code-update/src/nknu/archive/left_over.py").write_text("# not the repository's\n", encoding="utf-8")
    got = run("check_lab.sh", crawl, packed)
    assert got.returncode == 1 and "不一样的 2 个" in got.stdout
    assert "export-code/src/nknu/archive/export.py" in got.stdout and "left_over.py" in got.stdout


@linux_tools
def test_check_lab_says_whether_the_content_side_s_list_is_this_one(tmp_path, packed):
    # 2026-11-23: the site list has a third copy on the lab, the content side's (/work/config): its text and library
    # steps read from it which sites are round one, the panels the names. It is not the crawler's to replace, so it
    # is said and never counted: a difference in the crawler's own fields (caps, revisit) is no fault.
    crawl = lab(tmp_path)
    assert run("install.sh", crawl, packed).returncode == 0
    theirs = tmp_path / "work/config/archive_round1.json"
    theirs.parent.mkdir(parents=True)
    shutil.copy(REPO / "config/archive_round1.json", theirs)
    same = run("check_lab.sh", crawl, packed, CONTENT_LIST=str(theirs))
    assert same.returncode == 0 and "内容那边读的名单" in same.stdout and "和这一包的一样" in same.stdout
    theirs.write_text("{}", encoding="utf-8")
    other = run("check_lab.sh", crawl, packed, CONTENT_LIST=str(theirs))
    assert other.returncode == 0 and "和这一包的不一样" in other.stdout and "不一样的 0 个" in other.stdout
    assert "不一样：" not in other.stdout  # that wording is for the files that are counted
    unmounted = run("check_lab.sh", crawl, packed, CONTENT_LIST=str(tmp_path / "nowhere.json"))
    assert unmounted.returncode == 0 and "没挂进来" in unmounted.stdout


@linux_tools
def test_install_refuses_while_a_run_holds_the_ledger_and_a_package_that_is_not_whole(tmp_path, packed):
    crawl = lab(tmp_path)
    before = (crawl / "code-update/src/nknu/archive/watch.py").read_bytes()
    with (crawl / "archive/run.lock").open("a+") as fh:  # a run: the exclusive lock, its pid inside
        fcntl.flock(fh, fcntl.LOCK_EX)
        fh.write("4242")
        fh.flush()
        got = run("install.sh", crawl, packed)
    assert got.returncode == 1 and "run.lock" in got.stderr and "4242" in got.stderr
    broken = tmp_path / "broken"
    shutil.copytree(packed, broken)
    with (broken / "crawler-code.tar").open("ab") as fh:
        fh.write(b"x")
    got = run("install.sh", crawl, broken)
    assert got.returncode == 1 and "什么都没装" in got.stderr
    assert (crawl / "code-update/src/nknu/archive/watch.py").read_bytes() == before
    assert not list((crawl / "code-update/src/nknu/archive").glob("*.prev-*"))


@linux_tools
def test_install_says_when_a_copy_no_longer_loads(tmp_path, packed):
    crawl = lab(tmp_path)
    shutil.rmtree(crawl / "export-code/src/nknu/core")  # a copy missing what the crawler's files need
    got = run("install.sh", crawl, packed)
    assert got.returncode == 1 and "export-code" in got.stderr and "程序载不进来" in got.stderr
    assert not (crawl / "viewer-code").exists()  # and no third copy is made
    assert "code-update：换了 1 个档" in got.stdout  # the others were installed and say so


def _folder(root: Path, name: str, picture_url: str) -> Path:
    """An export folder of one page that shows one picture, whose row stands under picture_url."""
    import csv

    out = root / name
    (out / "pages").mkdir(parents=True)
    (out / "images").mkdir()
    (out / "pages/0001.html").write_text('<html><body><img src="https://x.example/new.jpg"></body></html>', encoding="utf-8")
    (out / "images/0001.jpg").write_bytes(b"jpg")
    cols = ["序号", "类型", "网址", "本地档案", "分类", "原因代码", "原因", "按钮编号"]
    with (out / "manifest.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerow({"序号": "1", "类型": "网页", "网址": "https://x.example/p", "本地档案": "pages/0001.html", "分类": "抓到了"})
        w.writerow({"序号": "2", "类型": "图片", "网址": picture_url, "本地档案": "images/0001.jpg", "分类": "抓到了"})
    return out


def test_the_comparison_names_a_page_whose_pictures_no_longer_pair_with_a_row_that_has_a_file(tmp_path):
    """No row lost its file, yet the page lost its picture: the row stands under an address the page's file does not
    show (11-19, what the content stage's guard caught and the row-by-row comparison did not)."""
    paired = _folder(tmp_path, "old", "https://x.example/new.jpg")
    unpaired = _folder(tmp_path, "new", "https://x.example/old.jpg")
    got = subprocess.run(["python3", str(KIT / "compare_export.py"), str(paired), str(unpaired)], capture_output=True,
                         text=True, check=False)
    assert got.returncode == 0, got.stderr
    assert "of them with a file under that address: 1 → 0" in got.stdout
    assert "pages that pair fewer pictures than before: 1" in got.stdout and "https://x.example/p" in got.stdout
    same = subprocess.run(["python3", str(KIT / "compare_export.py"), str(paired), str(paired)], capture_output=True,
                          text=True, check=False)
    assert "pages that pair fewer pictures than before: 0" in same.stdout
