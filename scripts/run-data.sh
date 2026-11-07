#!/usr/bin/env bash
# Supervisor for the live-data stack: recorder, YOLO counter (:8047), health server (:8052).
# bash 3.2 + the python3 the recorder already needs; no other deps.
#
#   scripts/run-data.sh                              # foreground; Ctrl-C stops only the supervisor
#   nohup scripts/run-data.sh >/dev/null 2>&1 &      # detached
#
# Every CHECK_EVERY s it
#   - starts a component that is not running (one already running is adopted, not restarted),
#   - restarts the recorder when a RECORDER_WATCH feed has had no data for DEAD_AFTER s,
#   - restarts the YOLO server when health has called it dead for DEAD_AFTER s,
# never judging a component within GRACE s of its (re)start. Idempotent: a second copy exits.
# Stopping the supervisor leaves the components running. Logs: data/logs/.
set -u
set -m  # background jobs get their own process group, so Ctrl-C here does not reach them

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOGS="${LOGS:-$ROOT/data/logs}"
CHECK_EVERY="${CHECK_EVERY:-30}"
DEAD_AFTER="${DEAD_AFTER:-180}"
GRACE="${GRACE:-180}"
RECORDER_WATCH="${RECORDER_WATCH:-jamcams tube line_status}"

RECORDER_CMD="${RECORDER_CMD:-exec caffeinate -i python3 $ROOT/recorder/record.py --yolo}"
RECORDER_MATCH="${RECORDER_MATCH:-$ROOT/recorder/record.py}"
YOLO_PORT="${YOLO_PORT:-8047}"
YOLO_DIR="${YOLO_DIR:-$HOME/orca/classroom-47/services/yolo}"
YOLO_CMD="${YOLO_CMD:-cd $YOLO_DIR && PORT=$YOLO_PORT exec .venv/bin/python server.py}"
YOLO_MATCH="${YOLO_MATCH:-port:$YOLO_PORT}"
HEALTH_CMD="${HEALTH_CMD:-exec python3 $ROOT/services/health/server.py}"
HEALTH_MATCH="${HEALTH_MATCH:-$ROOT/services/health/server.py}"
HEALTH_JSON_CMD="${HEALTH_JSON_CMD:-python3 $ROOT/services/health/health.py --json}"

COMPONENTS="recorder yolo health"
PIDFILE="$LOGS/supervisor.pid"
mkdir -p "$LOGS"

log() {
  local line
  line="$(date -u +%Y-%m-%dT%H:%M:%SZ) $*"
  echo "$line"
  echo "$line" >> "$LOGS/supervisor.log"
}

if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  echo "run-data: already running (pid $(cat "$PIDFILE")); see $LOGS/supervisor.log"
  exit 0
fi
echo $$ > "$PIDFILE"
trap 'rm -f "$PIDFILE"; log "supervisor: stopped (components left running)"; exit 0' INT TERM

cmd_of()   { local v; v="$(echo "$1" | tr a-z A-Z)_CMD";   echo "${!v}"; }
match_of() { local v; v="$(echo "$1" | tr a-z A-Z)_MATCH"; echo "${!v}"; }

pids() {
  case "$1" in
    port:*) lsof -nP -iTCP:"${1#port:}" -sTCP:LISTEN -t 2>/dev/null ;;
    *) pgrep -f "$1" ;;
  esac
}

start() {
  nohup bash -c "$(cmd_of "$1")" >> "$LOGS/$1.out" 2>&1 < /dev/null &
  printf -v "started_$1" '%s' "$(date +%s)"
  log "$1: started pid $! ($2)"
}

stop() {
  local match p i
  match="$(match_of "$1")"
  p="$(pids "$match")"
  [ -z "$p" ] && return 0
  kill $p 2>/dev/null
  for i in 1 2 3 4 5 6 7 8 9 10; do
    sleep 1
    [ -z "$(pids "$match")" ] && return 0
  done
  p="$(pids "$match")"
  [ -n "$p" ] && kill -9 $p 2>/dev/null
  return 0
}

restart() {
  log "$1: restarting ($2)"
  stop "$1"
  start "$1" "$2"
}

# Prints "<dead watched feeds or -> <yolo status>", or "? ?" when health itself fails.
health_summary() {
  bash -c "$HEALTH_JSON_CMD" 2>/dev/null | RECORDER_WATCH="$RECORDER_WATCH" DEAD_AFTER="$DEAD_AFTER" python3 -c '
import json, os, sys
try:
    d = json.load(sys.stdin)
except ValueError:
    print("? ?")
    sys.exit()
dead = [s for s in os.environ["RECORDER_WATCH"].split()
        if d["sources"].get(s, {}).get("age_s") is None
        or d["sources"][s]["age_s"] > float(os.environ["DEAD_AFTER"])]
print(",".join(dead) or "-", d.get("yolo", {}).get("status", "?"))
'
}

log "supervisor: pid $$ root=$ROOT check=${CHECK_EVERY}s dead_after=${DEAD_AFTER}s grace=${GRACE}s"
now="$(date +%s)"
for c in $COMPONENTS; do
  p="$(pids "$(match_of "$c")" | tr '\n' ' ')"
  if [ -n "$p" ]; then
    printf -v "started_$c" '%s' "$now"
    log "$c: adopted running pid(s) $p"
  fi
done
yolo_dead_since=0
health_failed=0

while true; do
  for c in $COMPONENTS; do
    [ -z "$(pids "$(match_of "$c")")" ] && start "$c" "not running"
  done

  read -r rec_dead yolo_status <<EOF
$(health_summary)
EOF
  now="$(date +%s)"
  if [ "$rec_dead" = "?" ]; then
    [ "$health_failed" = 0 ] && log "health: check failed; not judging feeds until it works"
    health_failed=1
  else
    [ "$health_failed" = 1 ] && log "health: check works again"
    health_failed=0
    if [ "$rec_dead" != "-" ] && [ $((now - started_recorder)) -ge "$GRACE" ]; then
      restart recorder "no data for >${DEAD_AFTER}s: $rec_dead"
    fi
    if [ "$yolo_status" = "dead" ]; then
      [ "$yolo_dead_since" = 0 ] && yolo_dead_since="$now"
      if [ $((now - yolo_dead_since)) -ge "$DEAD_AFTER" ] && [ $((now - started_yolo)) -ge "$GRACE" ]; then
        restart yolo "health dead for $((now - yolo_dead_since))s"
        yolo_dead_since=0
      fi
    else
      yolo_dead_since=0
    fi
  fi
  sleep "$CHECK_EVERY" &
  wait $!
done
