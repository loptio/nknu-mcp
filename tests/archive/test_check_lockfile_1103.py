"""`nknu check`'s PII scan and generated lock files (11-03, P98): uv.lock's sha256 digests hold runs of nine digits
that look like a student number; the lock file is generated, never written by a person, so it is not scanned. Every
other file is scanned as before. The number below is put together at run time, so that this file holds none."""

from __future__ import annotations

from nknu.admin.checks import check_pii

_DIGITS = "4" + "12345678"  # shaped like a student number (core.pii's student_id): made up


def test_a_lock_file_is_not_scanned_but_any_other_file_is(tmp_path):
    lock = f'[[package]]\nname = "x"\nsdist = {{ url = "https://x", hash = "sha256:ab{_DIGITS}", size = 1 }}\n'
    (tmp_path / "uv.lock").write_text(f'{lock}hash = "sha256:aa {_DIGITS} bb"\n', encoding="utf-8")
    (tmp_path / "notes.txt").write_text(f"學號 {_DIGITS}\n", encoding="utf-8")
    sub = tmp_path / "pkg"
    sub.mkdir()
    (sub / "uv.lock.txt").write_text(f"學號 {_DIGITS}\n", encoding="utf-8")  # not a lock file: only its name looks so
    hits = check_pii([tmp_path], root=tmp_path, allow={})
    assert hits == [f"pii: notes.txt: student_id:{_DIGITS}", f"pii: pkg/uv.lock.txt: student_id:{_DIGITS}"]
