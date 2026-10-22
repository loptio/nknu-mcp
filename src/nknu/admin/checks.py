"""`nknu check`: license scan, PII scan of fixtures/config, no disabled TLS verification, startup assertions.
Runs locally; CI is optional and never on the runtime path."""

from __future__ import annotations

import importlib.metadata
import re
import subprocess
import sys
from pathlib import Path

from nknu.core.pii import find_pii
from nknu.settings import REPO_ROOT, Settings

# LGPL libraries used unmodified as dynamic imports (allowed); everything GPL/AGPL fails
LGPL_ALLOWED = ("recurring-ical-events", "x-wr-timezone")
FORBIDDEN = {"pymupdf", "html2text", "ckip-transformers", "firecrawl", "firecrawl-py", "crawl4ai", "poppler"}
_TLS_OFF = re.compile(r"verify\s*=\s*False|" + "CERT_" + "NONE")
_TEXT_SUFFIXES = {".html", ".htm", ".ics", ".yaml", ".yml", ".json", ".txt", ".md", ".csv"}
_SELF_TESTS = {"test_checks.py"}  # writes the forbidden strings into temp files on purpose


def check_licenses() -> list[str]:
    out = []
    cmd = [sys.executable, "-m", "piplicenses", "--from=mixed", "--partial-match", "--fail-on=GPL;Affero",
           "--ignore-packages", *LGPL_ALLOWED, "nknu-facts"]
    r = subprocess.run(cmd, check=False, capture_output=True, text=True)
    if r.returncode != 0:
        out.append("license: " + (r.stderr.strip() or r.stdout.strip()).splitlines()[-1])
    installed = {(d.metadata["Name"] or "").lower() for d in importlib.metadata.distributions()}
    out += [f"license: forbidden package installed: {p}" for p in sorted(installed & FORBIDDEN)]
    return out


def check_pii(paths: list[Path]) -> list[str]:
    out = []
    for root in paths:
        for f in sorted(Path(root).rglob("*")):
            if not f.is_file() or f.suffix.lower() not in _TEXT_SUFFIXES:
                continue
            for hit in find_pii(f.read_text(errors="ignore")):
                if hit.startswith("staff_leave:") and any(c in hit for c in "OＯ○"):
                    continue  # already masked the school's way
                out.append(f"pii: {f}: {hit}")
    return out


def check_no_verify_false(roots: list[Path]) -> list[str]:
    out = []
    for root in roots:
        for f in sorted(Path(root).rglob("*.py")):
            if f.name in _SELF_TESTS or f.resolve() == Path(__file__).resolve():
                continue
            for n, line in enumerate(f.read_text().splitlines(), 1):
                if _TLS_OFF.search(line):
                    out.append(f"tls: {f}:{n}: certificate verification disabled")
    return out


def check_startup(settings: Settings) -> list[str]:
    out = []
    try:
        from zoneinfo import ZoneInfo

        import tzdata  # noqa: F401

        ZoneInfo("Asia/Taipei")
    except Exception as exc:  # noqa: BLE001
        out.append(f"startup: Asia/Taipei timezone unavailable ({exc}); install tzdata")
    if "unittest.mock" in sys.modules and "pytest" not in sys.modules:
        out.append("startup: unittest.mock is loaded in a production process")
    from nknu.crawl.tls import NKNU_CA_PATH, NKNU_CA_SHA256, pem_sha256

    if not NKNU_CA_PATH.exists() or pem_sha256(NKNU_CA_PATH) != NKNU_CA_SHA256:
        out.append(f"startup: bundled TWCA intermediate missing or changed: {NKNU_CA_PATH}")
    return out


def run_all_checks(settings: Settings) -> list[str]:
    return (check_licenses() + check_pii([REPO_ROOT / "tests" / "fixtures", REPO_ROOT / "config"])
            + check_no_verify_false([REPO_ROOT / "src", REPO_ROOT / "tests"]) + check_startup(settings))
