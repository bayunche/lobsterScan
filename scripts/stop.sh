#!/usr/bin/env bash
set -e
LOG_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/data/.logs"
for pid_file in "$LOG_DIR"/*.pid; do
  [[ -f "$pid_file" ]] || continue
  pid="$(cat "$pid_file")"
  if kill "$pid" 2>/dev/null; then
    echo "stopped $(basename "$pid_file" .pid) (pid $pid)"
  fi
  rm -f "$pid_file"
done
