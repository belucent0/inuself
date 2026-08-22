#!/bin/bash
set -euo pipefail

url="${1:?usage: wait-for-ai-gateway.sh URL COMMAND...}"
shift
: "${1:?usage: wait-for-ai-gateway.sh URL COMMAND...}"

check() {
  if command -v curl >/dev/null 2>&1; then
    curl -fs --max-time 3 "$url" >/dev/null 2>&1
  else
    python3 -c 'import sys, urllib.request; urllib.request.urlopen(sys.argv[1], timeout=3)' "$url" >/dev/null 2>&1
  fi
}

until check; do
  sleep 5
done

"$@" &
child=$!
terminate() {
  kill -TERM "$child" 2>/dev/null || true
  wait "$child" 2>/dev/null || true
  exit 143
}
trap terminate TERM INT

failures=0
while kill -0 "$child" 2>/dev/null; do
  sleep 5
  if check; then
    failures=0
  else
    ((failures += 1))
    if ((failures >= 3)); then
      kill -TERM "$child" 2>/dev/null || true
      wait "$child" 2>/dev/null || true
      exit 1
    fi
  fi
done
wait "$child"
