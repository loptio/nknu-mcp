"""A site goes back to the export the library in use was built from, and is named in ingest-hold.txt so that no night
moves it forward again, until what is wrong with its newer exports (or with how the build reads them) is mended and
checked. The note says why, what is waited for and who says when to let go; it is written above the site's line.
The list as it was is kept beside it (exports.txt.bak-hold-<site>). First used 11-18 (性别所: the export kept a
blurred preview of a picture; 特教中心: notices of several sections numbered alike).
Usage (in the image, /work writable): lab_hold_site.py SITE NOTE_FILE [--write]"""
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

if len(sys.argv) < 3 or sys.argv[1].startswith("-"):
    sys.exit("usage: lab_hold_site.py SITE NOTE_FILE [--write]")
SITE, note_file = sys.argv[1], Path(sys.argv[2])
note = " ".join(note_file.read_text(encoding="utf-8").split())
if not note:
    sys.exit("the note is empty: say why the site is held and what it waits for")
pipe = Path("/work/research/pipeline_2026-11-02")
prefix = SITE.replace("/", "_") + "-"


def line_of(path: Path) -> list[tuple[int, str]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [(i, text.strip()) for i, text in enumerate(lines)
            if not text.lstrip().startswith("#") and Path(text.strip()).name.startswith(prefix)]


official, listed = line_of(pipe / "exports-official.txt"), line_of(pipe / "exports.txt")
if len(official) != 1 or len(listed) != 1:
    sys.exit(f"expected one line each: the library's {official}, the list's {listed}")
want, (at, have) = official[0][1], listed[0]
if not (Path("/work") / want / "manifest.csv").is_file() and not (Path("/work") / want / "manifest.json").is_file():
    sys.exit(f"the folder the library in use was built from is gone: {want}")
hold = pipe / "ingest-hold.txt"
held = hold.read_text(encoding="utf-8") if hold.is_file() else ""
named = any(line.split()[:1] == [SITE] for line in held.splitlines() if not line.startswith("#"))
print(f"exports.txt line {at + 1}: {have}\n  -> {want}\nnamed in ingest-hold.txt: {named}")
if "--write" not in sys.argv:
    sys.exit(0)
if have != want:
    shutil.copy2(pipe / "exports.txt", pipe / f"exports.txt.bak-hold-{prefix.rstrip('-')}")
    lines = (pipe / "exports.txt").read_text(encoding="utf-8").splitlines()
    lines[at] = want
    tmp = pipe / "exports.txt.new"
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tmp.replace(pipe / "exports.txt")
if not named:
    day = datetime.now(timezone(timedelta(hours=8))).strftime("%m-%d %H:%M")
    with hold.open("a", encoding="utf-8") as f:
        f.write(("" if held.endswith("\n") or not held else "\n") + f"# {day}：{note}\n{SITE}\n")
print("written")
print(hold.read_text(encoding="utf-8"))
