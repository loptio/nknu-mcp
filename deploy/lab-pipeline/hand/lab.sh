# Sourced by the Mac-side scripts of this folder: how the lab PC is reached (ssh nknu-lab; its shell is cmd, so
# nothing with quotes, pipes or && is sent through it: the lab-side scripts of this folder do the looking).
D='set "DOCKER_CONFIG=%USERPROFILE%\.docker-ssh" &'
C="docker compose -f C:\\nknu-transfer\\build\\compose.yaml"
HAND=/work/deploy/lab-pipeline/hand
REPO=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)
TRIALS=$REPO/research/pipeline_2026-11-02/trials  # what a trial's checks said, fetched here (in no commit)
clean() { grep -v -i "orphan\|Container nknu-lab" | cut -c1-260; }
names() { ssh nknu-lab "$D docker ps --format {{.Names}}" 2>/dev/null | tr -d '\r'; }
# a look inside the image, /work read-only: lab_ro bash $HAND/lab_state.sh look
lab_ro() { ssh nknu-lab "$D docker run --rm -v nknu-work:/work:ro -v C:\\nknu-transfer\\out:/out -w /work -e NKNU_CONFIG_DIR=/work/config -e PYTHONPATH=/work/src nknu-pipeline $*" 2>/dev/null | tr -d '\r'; }
# the same with /work writable (the pause file, the hold list, a trial's name)
lab_rw() { ssh nknu-lab "$D docker run --rm -v nknu-work:/work -v C:\\nknu-transfer\\out:/out -w /work -e NKNU_CONFIG_DIR=/work/config -e PYTHONPATH=/work/src nknu-pipeline $*" 2>/dev/null | tr -d '\r'; }
# with the card and the models (the 33 questions, the real-data checks)
lab_gpu() { ssh nknu-lab "$D docker run --rm --gpus all --shm-size 8g -v nknu-work:/work -v nknu-models:/models -v C:\\nknu-transfer\\out:/out -w /work -e NKNU_CONFIG_DIR=/work/config -e PYTHONPATH=/work/src nknu-pipeline $*" 2>&1 | tr -d '\r'; }
ingest() { ssh nknu-lab "$D $C run --rm ingest $*" 2>&1 | clean; }
datasette_again() { ssh nknu-lab "$D docker compose -f C:\\nknu-transfer\\inspect\\compose.yaml restart datasette" 2>&1 | tail -1; }
# until no ingest container runs (two looks in a row; a lost connection is not an end): wait_ingest [hours]
wait_ingest() {
  local quiet=0 end=$(( $(date +%s) + ${1:-6} * 3600 )) got
  while [ "$quiet" -lt 2 ]; do
    sleep 90
    got=$(names) || got=ERR
    if [ -z "$got" ] || [ "$got" = ERR ]; then quiet=0; continue; fi
    if echo "$got" | grep -q nknu-lab-ingest-run; then quiet=0; else quiet=$((quiet + 1)); fi
    [ "$(date +%s)" -lt "$end" ] || { echo "the ingest still runs after ${1:-6} h"; return 8; }
  done
}
