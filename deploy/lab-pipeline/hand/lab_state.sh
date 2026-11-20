#!/usr/bin/env bash
# Small looks at (and two changes of) the library folder, called from the Mac's scripts so that no shell text has
# to survive ssh and cmd quoting (11-18; in the repository since 11-19). Usage (in the image, /work mounted):
#   lab_state.sh inode | paused | same NAME | pause | unpause | look [NAME] | guard [FILE] | outcome
cd /work/research/pipeline_2026-11-02 || exit 9
case "${1:-}" in
  inode) stat -c %i aca.db ;;
  paused) if [ -f ingest.pause ]; then echo paused; else echo open; fi ;;
  same) if [ aca.db -ef "${2:?NAME}.db" ]; then echo same; else echo different; fi ;;
  pause)  # the pause back in place: a run of the nightly's program then repoints and converts, and builds nothing
    if [ -f ingest.pause ]; then echo "paused already"
    else echo "$(TZ=CST-8 date '+%F %T') a trial is checked before it is promoted; hand/promote.sh takes this file away" > ingest.pause && echo "pause written"; fi ;;
  unpause) if [ -f ingest.pause ]; then rm -f ingest.pause && echo "pause taken away"; else echo "no pause file"; fi ;;
  outcome)  # the last night's outcome and step, one line
    python3 -c "import json; d = json.load(open('ingest-status.json', encoding='utf-8')); print(d.get('started_at'), d.get('outcome'), d.get('step'), d.get('error'), d.get('unexplained_left'))"
    ;;
  look)
    ls -li aca.db aca.prev.db ${2:+"$2.db"} aca.held.db ingest.pause* 2>/dev/null | cut -c1-120
    wc -l exports.txt exports-official.txt exports-prev.txt 2>/dev/null | head -3
    python3 - <<'PY'
import sqlite3
for name in ("aca.db", "aca.prev.db"):
    try:
        con = sqlite3.connect(f"file:{name}?mode=ro", uri=True)
        meta = dict(con.execute("select key, value from meta where key in ('built_at','schema')"))
        print(name, meta.get("built_at"), con.execute("select count(*) from docs").fetchone()[0], "docs",
              con.execute("select count(*) from sites").fetchone()[0], "sites")
    except Exception as e:  # a look only
        print(name, "unreadable:", type(e).__name__, e)
PY
    ;;
  guard)
    python3 - "${2:-aca.swap.json}" <<'PY'
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
left = d.get("departures") or {}
print("guard: swapped", d.get("swapped"), "held", d.get("held"), "too_many_left", d.get("too_many_left"),
      "school_wave", d.get("school_wave"), "collapsed", d.get("collapsed"))
print("left", left.get("left"), "explained", left.get("explained"), "unexplained", left.get("unexplained"),
      "by_site", left.get("by_site"))
PY
    ;;
  *) echo "usage: inode | paused | same NAME | pause | unpause | look [NAME] | guard [FILE] | outcome"; exit 2 ;;
esac
