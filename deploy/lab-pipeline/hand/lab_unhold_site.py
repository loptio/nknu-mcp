"""11-18 (session 4bd91945): a site named in ingest-hold.txt by lab_hold_site_1118.py is let go again: its line and
the note above it leave the file (the file as it was is kept as ingest-hold.txt.bak-1118un-<site>); the next ingest
then moves the site to its newest export by itself. Usage: lab_unhold_site.py SITE [--write]"""
import shutil
import sys
from pathlib import Path

site = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else sys.exit("usage: SITE [--write]")
hold = Path("/work/research/pipeline_2026-11-02/ingest-hold.txt")
lines = hold.read_text(encoding="utf-8").splitlines()
at = [i for i, line in enumerate(lines) if not line.startswith("#") and line.split()[:1] == [site]]
if len(at) != 1:
    sys.exit(f"{site} is named {len(at)} times in ingest-hold.txt")
drop = {at[0]} | ({at[0] - 1} if at[0] > 0 and lines[at[0] - 1].startswith("#") else set())
kept = [line for i, line in enumerate(lines) if i not in drop]
print("leaves:", [lines[i][:60] for i in sorted(drop)])
print("stays:", [line[:60] for line in kept])
if "--write" in sys.argv:
    shutil.copy2(hold, hold.with_name(f"ingest-hold.txt.bak-1118un-{site.split('.')[0]}"))
    tmp = hold.with_name("ingest-hold.txt.new")
    tmp.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
    tmp.replace(hold)
    print("written")
