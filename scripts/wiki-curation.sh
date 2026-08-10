#!/bin/bash
# wiki-curation.sh — Phase 2 curation cron wrapper for Memento
# Runs AFTER the nightly extraction pipeline (wiki-extract-pipeline.sh).
# Uses the same "extraction" lock to avoid interleaving.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOCK_SCRIPT="$SCRIPT_DIR/wiki-lock.sh"
LOCK_NAME="extraction"

# ── Env ──────────────────────────────────────────────────────────────────────
export LLM_API_BASE_URL="${LLM_API_BASE_URL:-http://127.0.0.1:8000/v1}"
export LLM_MODEL="${LLM_MODEL:-gemma-4-E4B-it-qat-mxfp4}"
export COMPACT_LLM_MAX_CALLS="${COMPACT_LLM_MAX_CALLS:-40}"
export LLM_API_KEY="${LLM_API_KEY:-}"

# ── Ensure Python has pyyaml ─────────────────────────────────────────────────
PYTHON_BIN="/usr/bin/python3"
if ! "$PYTHON_BIN" -c "import yaml" 2>/dev/null; then
    PYTHON_BIN="python3"
    if ! "$PYTHON_BIN" -c "import yaml" 2>/dev/null; then
        echo "FATAL: no python3 with pyyaml found. Install: pip3 install pyyaml" >&2
        exit 1
    fi
fi

# ── Kickstart oMLX if not running ────────────────────────────────────────────
OMLX_PLIST="gui/$(id -u)/com.omlx.server"
launchctl kickstart -k "$OMLX_PLIST" 2>/dev/null || true

# Wait up to 60 seconds for oMLX (server ~1s + model load ~8s, generous margin for 4am)
for i in $(seq 1 30); do
    if curl -s --max-time 2 "$LLM_API_BASE_URL/models" > /dev/null 2>&1; then
        break
    fi
    if [ "$i" -eq 30 ]; then
        echo "WARNING: oMLX not reachable at $LLM_API_BASE_URL after 60s, using --no-llm" >&2
        NO_LLM_FLAG="--no-llm"
    fi
    sleep 2
done

# ── Wait for extraction to release the shared lock, then run curation ────────
# wiki-lock.sh is fail-fast; this loop polls until the lock is free.
# NOTE: do NOT acquire/release the lock here — wiki-compact.py acquires it
# itself (acquire_lock()). If this wrapper also acquires it, wiki-compact's
# own acquire sees the mkdir-only lock dir (no pid, fresh mtime) as "held but
# not stale" and fails with "Cannot acquire lock: another wiki writer holds it".
# So this wrapper ONLY waits for the lock to be free; the actual acquire/
# release is owned by wiki-compact.py.
LOCK_NAME="extraction"
LOCK_RETRIES="${WIKI_CURATION_LOCK_RETRIES:-150}"
LOCK_SLEEP="${WIKI_CURATION_LOCK_SLEEP:-60}"
for attempt in $(seq 1 "$LOCK_RETRIES"); do
    if ! "$LOCK_SCRIPT" status "$LOCK_NAME" >/dev/null 2>&1; then
        break
    fi
    if [ "$attempt" -eq "$LOCK_RETRIES" ]; then
        echo "FATAL: extraction lock still held after $((LOCK_RETRIES * LOCK_SLEEP))s; curation not run" >&2
        exit 75
    fi
    sleep "$LOCK_SLEEP"
done

# wiki-compact.py acquires and releases the extraction lock itself.
"$PYTHON_BIN" "$HOME/.hermes/scripts/wiki-compact.py" ${NO_LLM_FLAG:-} "$@"
