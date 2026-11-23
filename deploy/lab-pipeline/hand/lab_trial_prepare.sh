#!/usr/bin/env bash
# Before a trial build that may be promoted (11-18; in the repository since 11-19): its name as a hard link of the library in use
# (the build's departure guard then compares the trial with it; a build replaces the name, never writes the file it
# links), and its folder with the library's site_checks.json in it (build.sh's site report then says which reminders
# are new against the library in use, as a night's does). Usage: lab_trial_prepare.sh NAME
cd /work/research/pipeline_2026-11-02 || exit 9
n=${1:?NAME}
[ -e "$n.db" ] && { echo "$n.db is there already"; exit 3; }
[ -d "$n" ] && { echo "$n/ is there already"; exit 3; }
rm -f "$n.swap.json"
ln aca.db "$n.db" && mkdir "$n" && { cp -p site_checks.json "$n/site_checks.json" 2>/dev/null || echo "no site_checks.json to compare with"; }
ls -li aca.db "$n.db" | cut -c1-90; ls "$n"
