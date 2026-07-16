#!/bin/bash
# wiki-lock — mutual exclusion for extraction & curation cron jobs.
# Usage: wiki-lock acquire <job-name>   # returns 0 on success, 1 if locked
#        wiki-lock release <job-name>   # releases the lock
#        wiki-lock check <job-name>     # 0=locked, 1=free

LOCKDIR="/tmp/wiki-locks"
mkdir -p "$LOCKDIR"

case "${1:-}" in
  acquire)
    if mkdir "$LOCKDIR/$2" 2>/dev/null; then
      echo "$2 acquired lock"
      exit 0
    else
      echo "$2 is already locked (held by $(cat "$LOCKDIR/$2/pid" 2>/dev/null || echo "unknown"))"
      exit 1
    fi
    ;;
  release)
    rm -rf "$LOCKDIR/$2"
    echo "$2 released lock"
    exit 0
    ;;
  check)
    if [ -d "$LOCKDIR/$2" ]; then
      exit 0
    else
      exit 1
    fi
    ;;
  *)
    echo "Usage: wiki-lock {acquire|release|check} <job-name>"
    exit 1
    ;;
esac