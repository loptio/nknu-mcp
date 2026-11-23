"""Old Office and OpenDocument files to docx/xlsx/pptx with LibreOffice headless (plan 2026-11-02 T3a), so Docling
reads them. Each call gets fresh folders under its workdir (profile, in, out): a profile of its own (no lock or
recovery state shared with another call; macros off, MacroSecurityLevel 3; BlockUntrustedRefererLinks on, a guard
against fetching what a document only links: on 11-02 LibreOffice 26.2 fetched neither an ODT's linked image nor an
RTF INCLUDEPICTURE from a loopback server, with the guard or without it), the input under an ASCII name, and an empty
output folder. soffice runs in a session of its own, so a timeout kills its whole process group (review focus
2: a dialog or a hang never outlives its file). Errors are ConvertError, whose text is the accounting reason."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import zipfile
from pathlib import Path

SOFFICE: str = (os.environ.get("NKNU_SOFFICE") or shutil.which("soffice")
                or "/Applications/LibreOffice.app/Contents/MacOS/soffice")
TARGET = {"doc": "docx", "odt": "docx", "rtf": "docx", "xls": "xlsx", "ods": "xlsx", "ppt": "pptx", "odp": "pptx"}
LO_TIMEOUT_S = 120
MAIN_PART = {"docx": "word/document.xml", "xlsx": "xl/workbook.xml", "pptx": "ppt/presentation.xml"}
_REAP_S = 10  # after SIGKILL, how long to wait for the process to be reaped

_SETTINGS = """<?xml version="1.0" encoding="UTF-8"?>
<oor:items xmlns:oor="http://openoffice.org/2001/registry" xmlns:xs="http://www.w3.org/2001/XMLSchema" \
xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
<item oor:path="/org.openoffice.Office.Common/Security/Scripting"><prop oor:name="MacroSecurityLevel" oor:op="fuse">\
<value>3</value></prop></item>
<item oor:path="/org.openoffice.Office.Common/Security/Scripting"><prop oor:name="DisableMacrosExecution" \
oor:op="fuse"><value>true</value></prop></item>
<item oor:path="/org.openoffice.Office.Common/Security/Scripting"><prop oor:name="BlockUntrustedRefererLinks" \
oor:op="fuse"><value>true</value></prop></item>
</oor:items>
"""


class ConvertError(Exception):
    """A conversion that gave no usable file; str() is the accounting reason (Simplified Chinese)."""


def _fresh(folder: Path) -> Path:
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    return folder


def _usable(path: Path, target: str) -> bool:
    try:
        with zipfile.ZipFile(path) as zf:
            return MAIN_PART[target] in zf.namelist()
    except (zipfile.BadZipFile, OSError):
        return False


def _kill_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)  # start_new_session: the group id is the child's pid
    except (ProcessLookupError, PermissionError):
        pass
    try:
        proc.wait(timeout=_REAP_S)
    except subprocess.TimeoutExpired:
        pass


def to_modern(src: Path, kind: str, workdir: Path, *, timeout_s: float = LO_TIMEOUT_S,
              soffice: str | None = None) -> Path:
    """src (an old Office, OpenDocument or RTF file of this kind) converted to TARGET[kind]; returns the new file,
    workdir/out/input.<target>. Raises ConvertError on a timeout, a missing soffice or no usable output."""
    target = TARGET.get(kind)
    if target is None:
        raise ConvertError(f"LibreOffice 不转这种格式（{kind or '未知'}）")
    workdir = Path(workdir).resolve()  # the profile goes in as a file:// URI, which needs an absolute path
    profile, inbox, outbox = (_fresh(workdir / name) for name in ("profile", "in", "out"))
    (profile / "user").mkdir()
    (profile / "user" / "registrymodifications.xcu").write_text(_SETTINGS, encoding="utf-8")
    source = inbox / f"input.{kind}"
    shutil.copyfile(src, source)
    exe = soffice or SOFFICE
    args = [exe, f"-env:UserInstallation={profile.as_uri()}", "--headless", "--norestore", "--nologo",
            "--nolockcheck", "--nodefault", "--convert-to", target, "--outdir", str(outbox), str(source)]
    with (workdir / "soffice.log").open("wb") as log:
        try:
            proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                    cwd=workdir, start_new_session=True)
        except OSError as e:
            raise ConvertError(f"LibreOffice 启动不了（{exe}）：{e.strerror or e}") from e
        try:
            code = proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            raise ConvertError(f"LibreOffice 超时（{timeout_s:g} 秒）") from None
        finally:
            if proc.poll() is None:  # a timeout, or an interrupt while waiting
                _kill_group(proc)
    out = outbox / f"input.{target}"
    if not _usable(out, target):
        raise ConvertError(f"LibreOffice 没产出可用的档（退出码 {code}）")
    return out
