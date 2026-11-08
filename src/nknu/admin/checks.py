"""`nknu check`: license scan, PII scan of every git-tracked text file (tests and docs included, F-check-scope), no
disabled TLS verification, startup assertions. Runs locally before a push; CI is optional and never on the runtime path."""

from __future__ import annotations

import importlib.metadata
import re
import subprocess
import sys
from pathlib import Path

from nknu.core.pii import find_pii
from nknu.settings import REPO_ROOT, Settings

# LGPL libraries used unmodified as dynamic imports (allowed; none since the ICS libraries left on 2026-10-24); everything
# GPL/AGPL fails
LGPL_ALLOWED: tuple[str, ...] = ()
FORBIDDEN = {"pymupdf", "html2text", "ckip-transformers", "firecrawl", "firecrawl-py", "crawl4ai", "poppler"}
_TLS_OFF = re.compile(r"verify\s*=\s*False|" + "CERT_" + "NONE")
# synthetic samples the tests use on purpose, per tracked file (find_pii hits); any other hit in these files still fails
PII_SAMPLES: dict[str, set[str]] = {
    "tests/core/test_pii.py": {"mobile:0912-345-678", "mobile:0912 345 678", "mobile:0912 345678", "national_id:A123456789",
                               "national_id:Q123456789", "student_id:400000000", "email:someone@gmail.com",
                               "email:someone.name@google.com", "student_id:４０００００００１", "mobile:０９１２３４５６７８",
                               "national_id:a123456789"},
    "tests/admin/test_checks.py": {"mobile:0912-345-678", "mobile:0987-654-321", "student_id:412345678"},
    "tests/server/test_app.py": {"mobile:0912-345-678"},
    # made-up Google calendar IDs and an event UID: calendar IDs are shaped like email addresses
    "tests/archive/test_google_api.py": {"email:c_0123456789abcdef@group.calendar.google.com", "email:nknuOOO@gmail.com",
                                         "email:holiday@group.v.calendar.google.com", "email:e1@google.com"},
    "tests/archive/test_export_1027.py": {"email:c_0123456789abcdef@group.calendar.google.com"},
    "tests/archive/test_tables_1106.py": {"email:o@example.test"},  # a made-up address in a table cell
    # the content stage's (11-08): made-up addresses, the school's public Google calendar ID (CLAUDE.md), and the
    # title of a regulation (修正學生請假…) that the staff-leave pattern takes for a leave note
    "src/nknu/content/build.py": {"staff_leave:(修正學生請假"},
    "tests/content/test_assemble.py": {"email:x@example.invalid"},
    "tests/content/test_build.py": {"email:nknubf@gmail.com"},
    "tests/content/test_corpus.py": {"email:nknubf@gmail.com"},
    "tests/content/test_links.py": {"email:aca@example.edu"},
    "tests/content/test_minutes_1108.py": {"staff_leave:（修正學生請假"},
    "tests/content/test_multisite_1108.py": {"email:nknubf@gmail.com"},
}
_SAMPLES_FILE = "src/nknu/admin/checks.py"  # this file: it spells the samples out above, and only those
_SELF_TESTS = {"test_checks.py"}  # writes the forbidden strings into temp files on purpose
# generated lock files of package versions and sha256 digests, never written by a person: a digest's nine digits look
# like a student number (11-03, P98: uv.lock); every other file is scanned as before
_LOCK_FILES = {"uv.lock"}


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


def tracked_files(root: Path = REPO_ROOT) -> list[Path]:
    """Every file git tracks: exactly what a push publishes (data/, docs/ and the other ignored folders never are)."""
    r = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], check=True, capture_output=True, text=True)
    return [root / name for name in r.stdout.split("\0") if name]


def check_pii(paths: list[Path], *, root: Path = REPO_ROOT, allow: dict[str, set[str]] | None = None) -> list[str]:
    """find_pii over every text file (no NUL byte) among paths (files, or folders scanned recursively); a hit listed in
    allow for that file (path relative to root) is an intended sample."""
    allow = PII_SAMPLES if allow is None else allow
    out = []
    files = sorted({f for p in map(Path, paths) for f in (p.rglob("*") if p.is_dir() else [p]) if f.is_file()})
    for f in files:
        if f.name in _LOCK_FILES:
            continue
        data = f.read_bytes()
        if b"\0" in data:
            continue  # binary
        rel = f.relative_to(root).as_posix() if f.is_relative_to(root) else str(f)
        allowed = set().union(*allow.values()) if rel == _SAMPLES_FILE else allow.get(rel, set())
        for hit in find_pii(data.decode("utf-8", errors="ignore")):
            if hit.startswith("staff_leave:") and any(c in hit for c in "OＯ○"):
                continue  # already masked the school's way
            if hit not in allowed:
                out.append(f"pii: {rel}: {hit}")
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
    try:
        pii = check_pii(tracked_files())
    except (OSError, subprocess.CalledProcessError) as exc:
        pii = [f"pii: cannot list the git-tracked files to scan ({exc})"]
    return check_licenses() + pii + check_no_verify_false([REPO_ROOT / "src", REPO_ROOT / "tests"]) + check_startup(settings)
