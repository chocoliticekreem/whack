#!/usr/bin/env bash
# The whole of OverWatch in one process tree: recorder, both simulations, the city exchange, the order-book
# Exchange, and the website. Used by the free Hugging Face Space (deploy/huggingface/), and works on any Linux or
# Mac box with python3 and a built apps/stream.
#
#   scripts/run-all.sh                  # website on :${PORT:-7860}
#   TFL_APP_KEY=... scripts/run-all.sh  # free key from api-portal.tfl.gov.uk; without it TfL caps us at 40 calls/min
#
# Each service restarts on its own if it exits. No camera counter (YOLO): it lives in Mirrorverse, outside this
# repo, so camera-based local markets and Camadel stay quiet; every TfL, aircraft and weather market runs.
# Logs: data/logs/<service>.log. Ctrl-C (or the container stopping) stops everything.
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOGS="$ROOT/data/logs"
PORT="${PORT:-7860}"
mkdir -p "$LOGS"
cd "$ROOT"

log() { echo "$(date -u +%H:%M:%S) run-all: $*"; }

# Runs a command forever: when it exits, wait 5 s and start it again.
keep() {
  local name="$1"
  shift
  (
    while true; do
      "$@" >> "$LOGS/$name.log" 2>&1
      log "$name exited ($?); restarting in 5 s"
      sleep 5
    done
  ) &
  log "$name started"
}

# A simulation server waits for POST /spawn. Wait for its /health, then spawn once: building 50 worlds can take
# minutes, and a retry on timeout would queue a second build.
spawn() {
  local port="$1" tries=0
  until curl -fsS -m 5 "http://127.0.0.1:$port/health" > /dev/null 2>&1; do
    tries=$((tries + 1))
    [ "$tries" -ge 60 ] && { log "simulation on :$port never came up; the site runs without it"; return; }
    sleep 5
  done
  if curl -fsS -m 1800 -X POST "http://127.0.0.1:$port/spawn" -d '{"n":50,"mode":"live"}' > /dev/null 2>&1; then
    log "simulation on :$port spawned (live)"
  else
    log "simulation on :$port failed to spawn; see data/logs"
  fi
}

# The simulations pick real data or their synthetic fixture when they start, so they wait for the recorder's
# first camera snapshot (a fresh container has none).
wait_for_data() {
  local tries=0
  until [ -n "$(ls -A "$ROOT/data/raw/jamcams" 2>/dev/null)" ]; do
    tries=$((tries + 1))
    [ "$tries" -ge 60 ] && { log "no camera data after 5 min; starting the simulations anyway"; return; }
    sleep 5
  done
}

trap 'log "stopping"; kill 0' INT TERM

keep recorder python3 recorder/record.py
keep city python3 services/city/server.py
keep exchange python3 services/city/venue_server.py
(
  wait_for_data
  keep vix env MULTIVERSE_PORT=8349 python3 services/multiverse/server.py
  keep sim env MULTIVERSE_PORT=8449 MULTIVERSE_SEED=seeds/central-london.json python3 services/multiverse/server.py
  spawn 8349
  spawn 8449
) &

log "website on :$PORT"
cd apps/stream
exec npx next start -H 0.0.0.0 -p "$PORT"
