"""The crawler's lab kit (deploy/lab-crawler; 11-19: what runs every night was only on two disks, research/ being out
of git): the files the lab PC runs are in the repository and name each other rightly; the package holds the crawler's
own files and nothing of another session's; install.sh puts it over the lab's three copies of the program keeping
what it replaces, and refuses a broken package and a ledger a run holds; check_lab.sh tells whether the lab's files
are the repository's. Made-up trees under tmp_path; nothing here connects anywhere."""

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
COPIES = ("code-update", "export-code", "viewer-code")
linux_tools = pytest.mark.skipif(shutil.which("sha256sum") is None, reason="no sha256sum here (the lab's image has it)")


def compose() -> dict:
    return yaml.safe_load((KIT / "compose.yaml").read_text(encoding="utf-8"))


def test_every_script_parses_and_the_two_windows_files_are_plain_ascii():
    for name in SCRIPTS:
        got = subprocess.run(["bash", "-n", str(KIT / name)], capture_output=True, text=True, check=False)
        assert got.returncode == 0, (name, got.stderr)
    for name in ("nightly_update.cmd", "nightly_hidden.vbs"):  # cmd reads them in the console's code page
        (KIT / name).read_bytes().decode("ascii")


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
    assert sorted(services) == ["nightly", "rejudge", "status", "stop", "viewer"]
    night = services["nightly"]
    assert night["image"] == "nknu-browser" and night["environment"]["PYTHONPATH"] == "/crawl/code-update/src"
    assert night["environment"]["TZ"] == "Asia/Taipei" and night["environment"]["NKNU_SCHOOL_LOCK"]
    mounts = night["volumes"]
    assert "nknu-crawl:/crawl" in mounts and "nknu-work:/work" in mounts and "D:/nknu-crawl/warc:/crawl/archive/warc" in mounts
    assert any(m.endswith("google.env:/run/secrets/google.env:ro") for m in mounts)
    for name in ("viewer", "stop", "status", "rejudge"):
        assert not any("secrets" in str(m) for m in services[name].get("volumes", [])), name
    for name in ("stop", "status", "rejudge"):  # a person's tools: the night's own program, no network
        assert services[name]["network_mode"] == "none" and services[name]["working_dir"] == "/crawl/code-update"
    assert services["viewer"]["ports"] == ["127.0.0.1:8877:8877"]
    assert "D:/nknu-crawl/warc:/crawl/archive/warc:ro" in services["viewer"]["volumes"]


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
    """A made-up /crawl: the three copies of the program as they are in the repository, with one file of the archive
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
def test_install_puts_the_package_over_the_three_copies_and_keeps_what_it_replaces(tmp_path, packed):
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


@linux_tools
def test_check_lab_names_each_file_that_is_not_the_repository_s(tmp_path, packed):
    crawl = lab(tmp_path)
    assert run("install.sh", crawl, packed).returncode == 0
    (crawl / "export-code/src/nknu/archive/export.py").write_text("# changed on the lab by hand\n", encoding="utf-8")
    (crawl / "viewer-code/src/nknu/archive/left_over.py").write_text("# not the repository's\n", encoding="utf-8")
    got = run("check_lab.sh", crawl, packed)
    assert got.returncode == 1 and "不一样的 2 个" in got.stdout
    assert "export-code/src/nknu/archive/export.py" in got.stdout and "left_over.py" in got.stdout


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
    shutil.rmtree(crawl / "viewer-code/src/nknu/core")  # a copy missing what the crawler's files need
    got = run("install.sh", crawl, packed)
    assert got.returncode == 1 and "viewer-code" in got.stderr and "程序载不进来" in got.stderr
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
