#!/bin/bash
set -euo pipefail

urls=()
while (($#)) && [[ "$1" != "--" ]]; do
  urls+=("$1")
  shift
done
[[ "${1:-}" == "--" ]] || { echo "missing -- before command" >&2; exit 2; }
shift
: "${1:?usage: restart-after-dependents.sh URL... -- COMMAND...}"

child=0
terminate() {
  ((child)) && kill -TERM "$child" 2>/dev/null || true
  ((child)) && wait "$child" 2>/dev/null || true
  exit 143
}
trap terminate TERM INT

all_down() {
  local url
  for url in "${urls[@]}"; do
    if curl -fs --max-time 3 "$url" >/dev/null 2>&1; then
      return 1
    fi
  done
}

while true; do
  "$@" &
  child=$!
  if wait "$child"; then status=0; else status=$?; fi
  echo "child exited with status $status; waiting for GPU dependents to stop" >&2
  until all_down; do sleep 5; done
  sleep 5
done
