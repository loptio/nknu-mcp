from typer.testing import CliRunner

from nknu.admin.checks import check_licenses, check_no_verify_false, check_pii, check_startup, tracked_files
from nknu.admin.cli import app
from nknu.settings import REPO_ROOT, load_settings


def test_verify_false_detected(tmp_path):
    (tmp_path / "bad.py").write_text("import httpx\nhttpx.get('https://x', verify=False)\n")
    (tmp_path / "ok.py").write_text("x = 1\n")
    hits = check_no_verify_false([tmp_path])
    assert len(hits) == 1 and "bad.py" in hits[0]


def test_cert_none_detected(tmp_path):
    (tmp_path / "bad.py").write_text("ctx.verify_mode = ssl.CERT_NONE\n")
    assert check_no_verify_false([tmp_path])


def test_unmasked_leave_detected(tmp_path):
    (tmp_path / "f.ics").write_text("SUMMARY:\n甲乙丙請假\n")
    (tmp_path / "ok.ics").write_text("SUMMARY:\n甲O丙請假\n")
    hits = check_pii([tmp_path])
    assert len(hits) == 1 and "f.ics" in hits[0]


def test_mobile_detected(tmp_path):
    (tmp_path / "x.html").write_text("<p>聯絡 0912-345-678</p>")
    assert check_pii([tmp_path])


def test_python_and_any_text_file_scanned(tmp_path):
    # F-check-scope: the student number in tests/core/test_pii.py was pushed because .py files were never scanned
    (tmp_path / "test_x.py").write_text('assert f("學號 412345678")\n')
    (tmp_path / "notes").write_text("call 0912-345-678\n")
    (tmp_path / "blob.bin").write_bytes(b"\x00\x01 0912-345-678")
    hits = check_pii([tmp_path], root=tmp_path, allow={})
    assert sorted(h.split(": ")[1] for h in hits) == ["notes", "test_x.py"]


def test_allowlist_is_per_file_and_value(tmp_path):
    (tmp_path / "a.py").write_text("x = '0912-345-678'\n")
    (tmp_path / "b.py").write_text("x = '0912-345-678'\n")
    (tmp_path / "c.py").write_text("x = '0912-345-678'; y = '0987-654-321'\n")
    allow = {"a.py": {"mobile:0912-345-678"}, "c.py": {"mobile:0912-345-678"}}
    hits = check_pii([tmp_path], root=tmp_path, allow=allow)
    assert sorted(hits) == ["pii: b.py: mobile:0912-345-678", "pii: c.py: mobile:0987-654-321"]


def test_scan_covers_every_tracked_file():
    files = {f.relative_to(REPO_ROOT).as_posix() for f in tracked_files()}
    assert {"tests/core/test_pii.py", "README.md", "deploy/env.example", "uv.lock", "src/nknu/core/pii.py"} <= files
    assert not any(f.startswith(("data/", "docs/", "evidence/", "prototypes/")) for f in files)


def test_startup_ok():
    assert check_startup(load_settings({})) == []


def test_licenses_clean():
    assert check_licenses() == []


def test_repo_check_passes():
    r = CliRunner().invoke(app, ["check"])
    assert r.exit_code == 0, r.output
