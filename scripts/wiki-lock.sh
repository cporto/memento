#!/usr/bin/env bash
# wiki-lock.sh — mkdir-based mutual exclusion for wiki writers.
#
# v6: lock root moved from /tmp to ~/.cache (override: WIKI_LOCK_DIR).
# Reasoning: macOS periodically purges /tmp (~3 days untouched), which could
# silently drop the lock in the middle of a long backfill and let a second
# writer in. ~/.cache persists across the purge cycle and reboots don't leave
# stale locks any worse than before (PID staleness check handles those).
#
# Usage: wiki-lock.sh acquire <name>   -> exit 0 if acquired, 1 if held
#        wiki-lock.sh release <name>
#        wiki-lock.sh status  <name>   -> exit 0 if held, 1 if free

set -u

LOCK_ROOT="${WIKI_LOCK_DIR:-$HOME/.cache/wiki-locks}"
CMD="${1:-}"
NAME="${2:-}"

if [[ -z "$CMD" || -z "$NAME" ]]; then
    echo "usage: $0 {acquire|release|status} <name>" >&2
    exit 64
fi

# Refuse path-traversal in lock names
case "$NAME" in
    */*|..*) echo "invalid lock name: $NAME" >&2; exit 64 ;;
esac

LOCK_DIR="$LOCK_ROOT/$NAME"

case "$CMD" in
    acquire)
        mkdir -p "$LOCK_ROOT"
        # mkdir is atomic: succeeds only if the dir doesn't exist
        if mkdir "$LOCK_DIR" 2>/dev/null; then
            exit 0
        fi
        exit 1
        ;;
    release)
        rm -rf "$LOCK_DIR"
        exit 0
        ;;
    status)
        [[ -d "$LOCK_DIR" ]] && exit 0 || exit 1
        ;;
    *)
        echo "unknown command: $CMD" >&2
        exit 64
        ;;
esac
