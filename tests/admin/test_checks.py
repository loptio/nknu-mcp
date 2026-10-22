from typer.testing import CliRunner

from nknu.admin.checks import check_licenses, check_no_verify_false, check_pii, check_startup
from nknu.admin.cli import app
from nknu.settings import load_settings


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


def test_startup_ok():
    assert check_startup(load_settings({})) == []


def test_licenses_clean():
    assert check_licenses() == []


def test_repo_check_passes():
    r = CliRunner().invoke(app, ["check"])
    assert r.exit_code == 0, r.output
