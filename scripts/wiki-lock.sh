#!/usr/bin/env bash
# wiki-lock.sh — mkdir-based mutual exclusion for wiki writers.
#
# v7: status is now staleness-aware. The pid file's process is probed with
# kill -0 (mirroring session-to-wiki.py acquire_lock()); a dead-pid lock is
# reported free AND removed, and a no-pid lock older than 1h is likewise
# treated stale. This prevents curation's wait-loop from polling forever on a
# lock orphaned by the extraction watchdog's SIGKILL. (v6's comment claiming a
# "PID staleness check handles those" was false — status had no such check.)
#
# Usage: wiki-lock.sh acquire <name>   -> exit 0 if acquired, 1 if held
#        wiki-lock.sh release <name>
#        wiki-lock.sh status  <name>   -> exit 0 if held, 1 if free (stale locks are freed + removed)

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
        # Staleness-aware status (mirrors session-to-wiki.py acquire_lock()).
        # exit 0 = held (live owner) ; exit 1 = free (no lock, OR stale removed).
        if [[ ! -d "$LOCK_DIR" ]]; then
            exit 1   # no lock dir -> free
        fi
        if [[ -f "$LOCK_DIR/pid" ]]; then
            owner="$(cat "$LOCK_DIR/pid" 2>/dev/null | tr -d '[:space:]')"
            # Probe the PID for EXISTENCE via `ps -p` (not `kill -0`).
            # Rationale: `kill -0` conflates "process is dead" (ESRCH) with
            # "process exists but I can't signal it" (EPERM) — e.g. a root-owned
            # writer seen by the hermes cron user returns EPERM and would be
            # falsely treated as DEAD, and its live lock removed (double-writer
            # corruption). `ps -p` reads the process table and needs no signal
            # permission, so it correctly reports root-owned live pids as alive
            # — matching session-to-wiki.py's os.kill(pid,0) semantics (EPERM !=
            # ESRCH). Alive (exists) -> held.
            if [[ "$owner" =~ ^[0-9]+$ ]] && ps -p "$owner" >/dev/null 2>&1; then
                exit 0   # held by a live process (any owner)
            fi
            # pid present but process dead / unreadable / non-numeric -> stale:
            # report free and remove the stale dir so a later acquire won't
            # re-discover it and curation's wait loop sees free immediately.
            rm -rf "$LOCK_DIR"
            exit 1
        fi
        # No pid file: fall back to dir-age heuristic (>1h stale, mirroring
        # Python's time.time() - dir mtime > 3600). macOS BSD find lacks -mmin,
        # so compute age via stat epoch seconds.
        now="$(date +%s)"
        mtime="$(stat -f %m "$LOCK_DIR" 2>/dev/null || echo 0)"
        age=$(( now - mtime ))
        if (( age > 3600 )); then
            rm -rf "$LOCK_DIR"
            exit 1
        fi
        # No pid and fresh (<1h): ambiguous -> treat as held to be safe.
        exit 0
        ;;
    *)
        echo "unknown command: $CMD" >&2
        exit 64
        ;;
esac
