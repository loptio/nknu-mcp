"""The nightly copy of the raw crawl files to the backup disk (2026-11-23: the working copy moved to another disk and
the files there were the only whole copy; some of the pages they hold the school has since removed). What the copy
promises: the backup only grows, the source is never written, a file counts only once it reads back the same, a
night that was missed is made up, and a morning reader can tell from one file whether the backup is behind."""
import fcntl
import json
import os

from nknu.archive import warcbackup


def _tree(root, files: dict[str, bytes]):
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(body)


def _files(root) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def _setup(tmp_path, source: dict[str, bytes], backup: dict[str, bytes]):
    root, dst = tmp_path / "archive", tmp_path / "backup"
    (root / "warc").mkdir(parents=True)
    dst.mkdir()
    _tree(root / "warc", source)
    _tree(dst, backup)
    return root, dst


def _run(root, dst, *more) -> int:
    return warcbackup.main(["--dir", str(root), "--to", str(dst), *more])


def _status(root) -> dict:
    return json.loads((root / "reports" / "warc-backup.json").read_text(encoding="utf-8"))


OLD = {"2026/11/21/wp-1.warc.gz": b"one" * 50, "2026/11/22/wp-2.warc.gz": b"two" * 70}
NEW = {"2026/11/23/wp-3.warc.gz": b"three" * 90, "2026/11/23/google-3.warc.gz": b"g" * 11}


def test_tonight_s_files_are_copied_and_the_source_is_not_touched(tmp_path):
    root, dst = _setup(tmp_path, OLD | NEW, OLD)
    before = {p: (p.stat().st_mtime_ns, p.read_bytes()) for p in (root / "warc").rglob("*") if p.is_file()}

    assert _run(root, dst) == 0

    assert _files(dst) == OLD | NEW
    assert {p: (p.stat().st_mtime_ns, p.read_bytes()) for p in (root / "warc").rglob("*") if p.is_file()} == before
    said = _status(root)
    assert said["state"] == "done" and said["ok"] is True and said["why"] is None
    assert (said["in_source"], said["in_backup"], said["behind"], said["copied"]) == (4, 4, 0, 2)
    assert said["copied_bytes"] == sum(len(b) for b in NEW.values()) and said["failed"] == []
    assert said["started_at"][:4] == "2026" or len(said["started_at"]) == 19  # local time, YYYY-MM-DD HH:MM:SS
    log = (root / "reports" / "warc-backup.log").read_text(encoding="utf-8").splitlines()
    assert len(log) == 1 and "新拷 2 个" in log[0]


def test_a_copied_file_keeps_the_time_the_crawl_wrote_it(tmp_path):
    root, dst = _setup(tmp_path, OLD | NEW, OLD)
    src = root / "warc" / "2026/11/23/wp-3.warc.gz"
    os.utime(src, (1_795_000_000, 1_795_000_000))

    assert _run(root, dst) == 0

    assert int((dst / "2026/11/23/wp-3.warc.gz").stat().st_mtime) == 1_795_000_000


def test_a_night_that_was_missed_is_made_up_the_next_time(tmp_path):
    root, dst = _setup(tmp_path, OLD | NEW, {"2026/11/21/wp-1.warc.gz": OLD["2026/11/21/wp-1.warc.gz"]})

    assert _run(root, dst) == 0

    assert _files(dst) == OLD | NEW and _status(root)["copied"] == 3


def test_nothing_in_the_backup_is_removed_when_the_source_lost_a_file(tmp_path):
    gone = {"2026/10/30/wp-0.warc.gz": b"the school has since removed these pages"}
    root, dst = _setup(tmp_path, OLD | NEW, OLD | gone)

    assert _run(root, dst) == 0  # the backup holds everything the source has: it is not behind

    assert _files(dst) == OLD | NEW | gone
    said = _status(root)
    assert said["only_in_backup"] == 1 and said["ok"] is True and "1 个" in said["message"]


def test_a_backup_file_of_another_size_is_kept_aside_and_the_source_s_copied(tmp_path):
    cut = {"2026/11/22/wp-2.warc.gz": b"two" * 3}
    root, dst = _setup(tmp_path, OLD, OLD | cut)

    assert _run(root, dst) == 0

    now = _files(dst)
    assert now["2026/11/22/wp-2.warc.gz"] == OLD["2026/11/22/wp-2.warc.gz"]
    aside = [k for k in now if k.startswith("2026/11/22/wp-2.warc.gz.old-")]
    assert len(aside) == 1 and now[aside[0]] == b"two" * 3  # the one that was there is not thrown away
    said = _status(root)
    assert said["set_aside"] == 1 and said["copied"] == 1 and said["only_in_backup"] == 0
    assert _run(root, dst) == 0 and _status(root)["copied"] == 0  # and the kept one is not taken for a stray file


def test_an_empty_backup_folder_is_not_filled_unless_told_it_is_the_first_time(tmp_path):
    # a mount path typed wrong: Docker makes an empty folder and says nothing; a second whole copy would go there
    root, dst = _setup(tmp_path, OLD | NEW, {})

    assert _run(root, dst) == 3

    assert _files(dst) == {}
    said = _status(root)
    assert said["ok"] is False and said["why"] == "backup_folder_empty" and said["behind"] == 4

    assert _run(root, dst, "--first") == 0
    assert _files(dst) == OLD | NEW and _status(root)["ok"] is True


def test_an_empty_source_folder_is_said_not_passed_as_nothing_to_do(tmp_path):
    root, dst = _setup(tmp_path, {}, OLD)

    assert _run(root, dst) == 3

    said = _status(root)
    assert said["ok"] is False and said["why"] == "source_empty" and _files(dst) == OLD


def test_no_copy_while_a_run_holds_the_ledger(tmp_path):
    root, dst = _setup(tmp_path, OLD | NEW, OLD)
    with open(root / "run.lock", "a+") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)

        assert _run(root, dst) == 1

        assert _files(dst) == OLD
        said = _status(root)
        assert said["ok"] is False and said["why"] == "run_in_progress" and said["behind"] == 2 and said["copied"] == 0
    assert _run(root, dst) == 0 and _files(dst) == OLD | NEW  # the next time makes it up


def test_a_copy_that_does_not_read_back_the_same_is_not_kept(tmp_path, monkeypatch):
    root, dst = _setup(tmp_path, OLD | NEW, OLD)
    real = warcbackup._digest
    monkeypatch.setattr(warcbackup, "_digest", lambda p: "0" * 64 if p.name.startswith("wp-3") else real(p))

    assert _run(root, dst) == 1

    assert _files(dst) == OLD | {"2026/11/23/google-3.warc.gz": NEW["2026/11/23/google-3.warc.gz"]}  # no .part left
    said = _status(root)
    assert said["ok"] is False and said["why"] == "copy_failed" and said["behind"] == 1 and said["copied"] == 1
    assert said["failed"][0]["file"] == "2026/11/23/wp-3.warc.gz" and "读回来" in said["failed"][0]["why"]
    assert "落后 1 个" in said["message"]

    monkeypatch.setattr(warcbackup, "_digest", real)
    assert _run(root, dst) == 0 and _files(dst) == OLD | NEW


def test_a_file_still_being_written_is_left_for_the_next_time(tmp_path, monkeypatch):
    root, dst = _setup(tmp_path, OLD | NEW, OLD)
    real = warcbackup._copy

    def copy_while_the_crawl_writes(src, part):
        out = real(src, part)
        if src.name.startswith("wp-3"):
            with open(src, "ab") as fh:
                fh.write(b"one more record")
        return out

    monkeypatch.setattr(warcbackup, "_copy", copy_while_the_crawl_writes)

    assert _run(root, dst) == 1

    assert "2026/11/23/wp-3.warc.gz" not in _files(dst)
    assert "还在写" in _status(root)["failed"][0]["why"]


def test_the_status_says_running_until_the_copy_ends(tmp_path, monkeypatch):
    # a copy killed halfway (the time limit, a reboot) leaves this word, not the last night's good result
    root, dst = _setup(tmp_path, OLD | NEW, OLD)
    real, seen = warcbackup._copy, []

    def look(src, part):
        seen.append(_status(root))
        return real(src, part)

    monkeypatch.setattr(warcbackup, "_copy", look)

    assert _run(root, dst) == 0

    assert seen and seen[0]["state"] == "running" and seen[0]["ok"] is None and seen[0]["ended_at"] is None
    assert _status(root)["state"] == "done" and _status(root)["ended_at"]


def test_check_only_looks(tmp_path, capsys):
    root, dst = _setup(tmp_path, OLD | NEW, OLD)

    assert _run(root, dst, "--check") == 1

    assert _files(dst) == OLD and not (root / "reports").exists()
    assert "2 个" in capsys.readouterr().out

    _tree(dst, NEW)
    assert _run(root, dst, "--check") == 0
    assert "都有" in capsys.readouterr().out and not (root / "reports").exists()
