"""to_modern: LibreOffice turns .doc/.xls/.ppt/.odt/.ods/.odp/.rtf into docx/xlsx/pptx in a profile of its own, killed
whole when it overruns (plan 2026-11-02 T3a, review focus 2)."""

from __future__ import annotations

import http.server
import os
import shutil
import threading
import time
import zipfile
from pathlib import Path

import pytest

from nknu.totext import libreoffice
from nknu.totext.libreoffice import TARGET, ConvertError, to_modern
from tests.totext.fixtures import zip_of


def _fake_soffice(tmp_path, body: str):
    script = tmp_path / "fake-soffice"
    script.write_text("#!/bin/sh\n" + body)
    script.chmod(0o755)
    return str(script)


def _gone(pid: int, within: float = 3.0) -> bool:
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.05)
    return False


def test_targets():
    assert TARGET == {"doc": "docx", "odt": "docx", "rtf": "docx", "xls": "xlsx", "ods": "xlsx", "ppt": "pptx",
                      "odp": "pptx"}
    assert libreoffice.LO_TIMEOUT_S == 120


def test_command_has_its_own_profile_and_flags(tmp_path, monkeypatch):
    calls = []

    class FakePopen:
        def __init__(self, args, **kwargs):
            calls.append((list(args), kwargs))
            out = args[args.index("--outdir") + 1]
            with zipfile.ZipFile(os.path.join(out, "input.docx"), "w") as zf:
                zf.writestr("word/document.xml", "<w:document/>")
            self.pid, self.returncode = 0, 0

        def wait(self, timeout=None):
            return 0

        def poll(self):
            return 0

    monkeypatch.setattr(libreoffice.subprocess, "Popen", FakePopen)
    src = tmp_path / "舊檔.doc"
    src.write_bytes(b"old word file")
    work = tmp_path / "work"
    out = to_modern(src, "doc", work, soffice="/x/soffice")
    [(args, kwargs)] = calls
    profile = work / "profile"
    assert args[:11] == ["/x/soffice", f"-env:UserInstallation={profile.as_uri()}", "--headless", "--norestore",
                         "--nologo", "--nolockcheck", "--nodefault", "--convert-to", "docx", "--outdir",
                         str(work / "out")]
    assert args[11] == str(work / "in" / "input.doc") and (work / "in" / "input.doc").read_bytes() == b"old word file"
    assert args[1].startswith("-env:UserInstallation=file://") and args[1].endswith("/profile")
    assert kwargs["start_new_session"] is True
    assert out == work / "out" / "input.docx"
    settings = (profile / "user" / "registrymodifications.xcu").read_text()
    assert "/org.openoffice.Office.Common/Security/Scripting" in settings
    assert '"MacroSecurityLevel"' in settings and "<value>3</value>" in settings
    assert '"DisableMacrosExecution"' in settings and "<value>true</value>" in settings
    assert '<prop oor:name="BlockUntrustedRefererLinks" oor:op="fuse"><value>true</value>' in settings


def test_relative_workdir_still_gives_an_absolute_profile(tmp_path, monkeypatch):
    # main session 11-03 02:00: the full run passed a relative --out, so the work folder was relative and
    # profile.as_uri() raised on every old Office file (29 failed)
    calls = []

    class FakePopen:
        def __init__(self, args, **kwargs):
            calls.append(list(args))
            out = args[args.index("--outdir") + 1]
            with zipfile.ZipFile(os.path.join(out, "input.docx"), "w") as zf:
                zf.writestr("word/document.xml", "<w:document/>")
            self.pid, self.returncode = 0, 0

        def wait(self, timeout=None):
            return 0

        def poll(self):
            return 0

    monkeypatch.setattr(libreoffice.subprocess, "Popen", FakePopen)
    monkeypatch.chdir(tmp_path)
    src = tmp_path / "舊檔.doc"
    src.write_bytes(b"old word file")
    out = to_modern(src, "doc", Path("rel") / "work", soffice="/x/soffice")
    [args] = calls
    assert args[1] == f"-env:UserInstallation={(tmp_path / 'rel' / 'work' / 'profile').resolve().as_uri()}"
    assert os.path.isabs(args[args.index("--outdir") + 1]) and os.path.isabs(args[-1])
    assert out.resolve() == (tmp_path / "rel" / "work" / "out" / "input.docx").resolve()


def test_timeout_kills_and_says_why(tmp_path):
    child = tmp_path / "child.pid"
    script = _fake_soffice(tmp_path, f'sleep 30 &\necho $! > "{child}"\nwait\n')
    src = tmp_path / "a.doc"
    src.write_bytes(b"x")
    started = time.monotonic()
    with pytest.raises(ConvertError) as caught:
        to_modern(src, "doc", tmp_path / "work", timeout_s=1, soffice=script)
    assert str(caught.value) == "LibreOffice 超时（1 秒）"
    assert time.monotonic() - started < 5
    assert _gone(int(child.read_text().strip()))  # the whole process group was killed, not only the script


def test_no_usable_output_is_an_error(tmp_path):
    script = _fake_soffice(tmp_path, "exit 0\n")
    src = tmp_path / "a.xls"
    src.write_bytes(b"x")
    with pytest.raises(ConvertError, match="没产出可用的档"):
        to_modern(src, "xls", tmp_path / "work", soffice=script)


def test_output_without_its_main_part_is_an_error(tmp_path):
    # a ZIP named input.xlsx that holds no xl/workbook.xml is no workbook
    made = zip_of(tmp_path / "made.xlsx", {"docProps/app.xml": b"<x/>"})
    script = _fake_soffice(tmp_path, f'cp "{made}" "{tmp_path}/work/out/input.xlsx"\n')
    src = tmp_path / "a.ods"
    src.write_bytes(b"x")
    with pytest.raises(ConvertError, match="没产出可用的档"):
        to_modern(src, "ods", tmp_path / "work", soffice=script)


def test_missing_soffice_is_an_error(tmp_path):
    src = tmp_path / "a.doc"
    src.write_bytes(b"x")
    with pytest.raises(ConvertError, match="LibreOffice"):
        to_modern(src, "doc", tmp_path / "work", soffice=str(tmp_path / "no-such-soffice"))


def test_unknown_kind_is_an_error(tmp_path):
    src = tmp_path / "a.pdf"
    src.write_bytes(b"%PDF")
    with pytest.raises(ConvertError):
        to_modern(src, "pdf", tmp_path / "work", soffice="/x/soffice")


def test_workdir_parts_are_fresh(tmp_path):
    # a docx left in out/ by an earlier call is never taken for this call's output
    work = tmp_path / "work"
    (work / "out").mkdir(parents=True)
    zip_of(work / "out" / "input.docx", {"word/document.xml": b"<x/>"})
    script = _fake_soffice(tmp_path, "exit 0\n")
    src = tmp_path / "a.doc"
    src.write_bytes(b"x")
    with pytest.raises(ConvertError, match="没产出可用的档"):
        to_modern(src, "doc", work, soffice=script)


_ODT_CONTENT = """<?xml version="1.0" encoding="UTF-8"?>
<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" \
xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0" \
xmlns:xlink="http://www.w3.org/1999/xlink" xmlns:svg="urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0" \
office:version="1.2"><office:body><office:text><text:p>圖<draw:frame draw:name="i1" svg:width="1cm" svg:height="1cm" \
text:anchor-type="as-char"><draw:image xlink:href="{url}" xlink:type="simple" xlink:show="embed" xlink:actuate="onLoad"/>\
</draw:frame></text:p></office:text></office:body></office:document-content>"""
_ODT_MANIFEST = """<?xml version="1.0" encoding="UTF-8"?>
<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" manifest:version="1.2">\
<manifest:file-entry manifest:full-path="/" manifest:media-type="application/vnd.oasis.opendocument.text"/>\
<manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/></manifest:manifest>"""


@pytest.mark.skipif(shutil.which("soffice") is None, reason="no LibreOffice")
def test_real_conversion_fetches_no_linked_image(tmp_path):
    # an image a document only links (ODT draw:image, RTF INCLUDEPICTURE) is never fetched: here from a loopback server
    hits = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            self.send_response(404)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        odt = tmp_path / "linked.odt"
        with zipfile.ZipFile(odt, "w") as zf:
            zf.writestr("mimetype", "application/vnd.oasis.opendocument.text")
            zf.writestr("content.xml", _ODT_CONTENT.format(url=f"{url}/odt.png"))
            zf.writestr("META-INF/manifest.xml", _ODT_MANIFEST)
        rtf = tmp_path / "linked.rtf"
        rtf.write_text("{\\rtf1\\ansi x {\\field{\\*\\fldinst INCLUDEPICTURE \"" + url + "/rtf.png\" \\\\d}{\\fldrslt}}}",
                       encoding="ascii")
        to_modern(odt, "odt", tmp_path / "w1")
        to_modern(rtf, "rtf", tmp_path / "w2")
    finally:
        server.shutdown()
    assert hits == []


@pytest.mark.skipif(shutil.which("soffice") is None, reason="no LibreOffice")
def test_real_rtf_to_docx(tmp_path):
    import docx

    src = tmp_path / "t.rtf"
    src.write_text("{\\rtf1\\ansi\\uc0\\u28204\\u35430}", encoding="ascii")
    out = to_modern(src, "rtf", tmp_path / "work")
    assert "測試" in "".join(p.text for p in docx.Document(out).paragraphs)
