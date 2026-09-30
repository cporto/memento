#!/bin/bash
# wiki-extract-pipeline.sh — Full extraction pipeline for nightly cron.
# Uses OpenRouter DeepSeek V4 Flash (fast, cheap, always available).
# Prevents macOS sleep during extraction with caffeinate.
#
# This wrapper is DESIGNED FOR CRON. When running manually for backfill,
# use session-to-wiki.py --auto --reprocess --max N directly instead.

set -euo pipefail

# --max N: how many unprocessed sessions to process this run (default 1, matching
# the nightly safety cap). The catch-up cron passes --max to burn down the backlog
# in daylight only. Routing through this wrapper (not bare session-to-wiki.py)
# keeps DeepInfra routing, enrich bounds, deadlines, and the watchdog intact.
MAX_SESSIONS="${MEMENTO_MAX_SESSIONS:-1}"
RETRY_SESSION="${MEMENTO_RETRY_SESSION:-}"
for arg in "$@"; do
  case "$arg" in
    --max=*) MAX_SESSIONS="${arg#--max=}" ;;
    --max) MAX_SESSIONS="${2:-1}"; shift ;;
    --retry-session=*) RETRY_SESSION="${arg#--retry-session=}" ;;
    --retry-session) RETRY_SESSION="${2:-}"; shift ;;
  esac
done
export MEMENTO_MAX_SESSIONS="$MAX_SESSIONS"
[ -n "$RETRY_SESSION" ] && export MEMENTO_RETRY_SESSION="$RETRY_SESSION"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Source the .env file for API keys (OpenRouter, Fireworks, etc.)
# Note: .env has unquoted paths with spaces, so use eval per-line.
if [ -f "$HOME/.hermes/.env" ]; then
  set -a
  while IFS= read -r line; do
    case "$line" in
      \#*|'') continue ;;
      *=*) eval "export $line" 2>/dev/null || true ;;
    esac
  done < "$HOME/.hermes/.env"
  set +a
fi

echo "[$(date)] Starting session-to-wiki auto extraction (DeepInfra DeepSeek V4 Flash, max $MAX_SESSIONS)..." >&2
# Extraction regularly exceeds the old 4 AM curation start. Curation now waits
# on the shared lock, so this remains safe even when extraction overruns.

# PRIMARY: DeepInfra (cheaper + comparably fast; live-probed ~1.4s on the pinned
# model). Falls back to Fireworks (also fast, ~0.9-1.7s) on error. Both serve the
# same dated snapshot deepseek-v4-flash-0731.
#
# ERROR-AWARE ROUTING (fixes the 2026-09-20 402-billing bug): the old block keyed
# on KEY PRESENCE ONLY, so a billing-blocked DeepInfra key (still "present") never
# tripped a fallback and HTTP 402 "inference prohibited, reached user-set limit"
# hammered the SAME dead provider on every call. We now probe for a 402/unreachable
# primary and flip to Fireworks, and record a provider-block (with TTL) so a
# 402-locked provider is never re-hammered. Shared router (also used by
# memento-retry-runner.sh's cloud leg) is in memento-cloud-router.sh.
DIKEY="${DEEPINFRA_API_KEY:-}"
. "$SCRIPT_DIR/memento-cloud-router.sh"
route_cloud
# Keep the per-call deadline generous (was 300s default causing 2x double-trips).
export LLM_CALL_DEADLINE_SEC="${LLM_CALL_DEADLINE_SEC:-600}"
# Keep structured extraction calls below the provider output ceiling.
export LLM_MAX_TOKENS="32768"
export LLM_ALLOW_THINKING="false"
export MEMENTO_TRANSCRIPT_BUDGET="120000"
export MEMENTO_ESTIMATOR_SEC_PER_CALL="10"
export LLM_TIMEOUT_CONNECT="30"
export LLM_TIMEOUT_READ="600"
# Intermittent JSON glitches (truncation, unescaped-quote corruption, empty
# content) are handled by bounded retries in the extractor itself. Keep
# response_format OFF: a strict schema risks ambiguous empty generations.
export MEMENTO_JSON_SCHEMA="0"
# Verified enrich-context bounds (mirror memento-retry-runner.sh): the code
# defaults (40k/8/6k/24) produce prompts larger than deepseek-flash will answer,
# causing the recurring "Empty content in LLM response" failure on enrichment.
export MEMENTO_ENRICH_PAGE_CONTEXT_CAP="16000"
export MEMENTO_ENRICH_PAGES_MAX="4"
export MEMENTO_ENRICH_PAGE_CHAR_CAP="4000"
export MEMENTO_ENRICH_CANDIDATE_FACTS_MAX="12"

EXIT_CODE=0

# Inactivity-based watchdog (fusion-improved 2026-08-23). The real stall was a
# single unbounded LLM read — per-recv socket timeout at session-to-wiki.py's
# requests.post, now fixed at the source via LLM_CALL_DEADLINE_SEC (total
# per-call deadline). This watchdog is the BACKSTOP for any residual wedge
# (non-LLM subprocess, kernel, DNS, macOS sleep race). It keys on PROGRESS, not
# wall-clock: session-to-wiki.py touches the wiki tree on every commit/heartbeat,
# so if the newest .md mtime stays unchanged for > MEMENTO_STALL_MIN minutes the
# run is genuinely stalled -> kill it (by process name, robust to caffeinate
# exec/fork, then by parent, escalated to KILL) and surface a non-zero exit so
# cron records the failure instead of silently wedging. A slow-but-alive run
# (commits keep landing) is NEVER killed. Default 45 min of zero wiki writes is
# an unambiguous stall; keep a 240-min whole-run ceiling as a last-resort tripwire.
STALL_MIN="${MEMENTO_STALL_MIN:-45}"
WIKI_DIR="$HOME/wiki"
# Lock release plumbing for the watchdog kill path and the wrapper EXIT trap.
# The extraction lock is shared with curation (wiki-curation.sh polls it, and
# wiki-compact.py acquires/releases it). Wrapping around the extraction release
# must NEVER clobber a lock live-held by another writer.
LOCK_NAME="extraction"
# Per-sid lock for giant/retry runs so PARALLEL finishers don't collide on one mutex;
# the shared 'extraction' lock remains for the normal batch path (3am/4am/10am).
if [ -n "$RETRY_SESSION" ]; then
  LOCK_NAME="extraction-${RETRY_SESSION}"
fi
LOCK_SCRIPT="$SCRIPT_DIR/wiki-lock.sh"
WIKI_LOCK_DIR="${WIKI_LOCK_DIR:-$HOME/.cache/wiki-locks}"
WATCHDOG_MAX_MIN="${MEMENTO_WATCHDOG_MIN:-240}"
STALL_SECS=$(( STALL_MIN * 60 ))
WATCHDOG_MAX_SECS=$(( WATCHDOG_MAX_MIN * 60 ))
# Hard clock-abort, ONLY when set (catch-up mode). Format HHMM (e.g. 0230 = 2:30am).
# The catch-up sets this so it always kills and releases the lock before the 3am
# nightly extraction (which is fail-fast). The nightly run (default) leaves it
# EMPTY -> the clock gate is DISABLED, so 3am extraction is never aborted by this.
ABORT_AT="${MEMENTO_ABORT_BEFORE_HHMM:-}"
# Spawn the watchdog (background python). Args passed AFTER -c string:
#   sys.argv[1]=stall_secs  sys.argv[2]=wrapper_pid($$)  sys.argv[3]=wiki_root
#   sys.argv[4]=max_secs  sys.argv[5]=abort_hhmm ('' = disabled)
#   sys.argv[6]=WIKI_LOCK_DIR  sys.argv[7]=lock script path (release on kill)
/usr/bin/python3 -c "
import subprocess, sys, time, os

def kill_extractor(parent):
    # kill by process name (robust to caffeinate exec vs fork) then by parent,
    # escalate to KILL. pkill -f matches the stuck session-to-wiki.py.
    subprocess.run(['pkill','-TERM','-f',r'session-to-wiki\.py'], check=False, capture_output=True)
    subprocess.run(['pkill','-TERM','-P',parent], check=False, capture_output=True)
    time.sleep(2)
    subprocess.run(['pkill','-KILL','-f',r'session-to-wiki\.py'], check=False, capture_output=True)
    subprocess.run(['pkill','-KILL','-P',parent], check=False, capture_output=True)
    # SIGKILL cannot be trapped, so session-to-wiki.py's own release trap will
    # NOT run and the extraction lock would be orphaned with a dead pid. Release
    # the extraction lock explicitly so curation's wait-loop and the next nightly
    # run (FIXED status treats dead-pid locks as stale) don't poll forever.
    try:
        rel_env = dict(os.environ)
        # argv[6]=WIKI_LOCK_DIR, argv[7]=lock script path (see spawn below).
        if len(sys.argv) > 7 and sys.argv[7]:
            rel_env['WIKI_LOCK_DIR'] = sys.argv[6]
            _lock = sys.argv[8] if len(sys.argv) > 8 and sys.argv[8] else 'extraction'
            subprocess.run([sys.argv[7],'release',_lock],
                           check=False, capture_output=True, env=rel_env)
    except Exception as e:
        print('WATCHDOG: lock release failed: %s' % e, flush=True)
    print('WATCHDOG: terminated extraction ('+reason+')', flush=True)

def newest_mtime(root):
    newest = 0.0
    for base, _, files in os.walk(root):
        for f in files:
            p = os.path.join(base, f)
            try:
                m = os.path.getmtime(p)
                if m > newest:
                    newest = m
            except OSError:
                pass
    return newest

try:
    stall_secs = float(sys.argv[1])
except (ValueError, IndexError):
    stall_secs = 2700.0
parent = sys.argv[2]
root = sys.argv[3]
max_secs = float(sys.argv[4]) if len(sys.argv) > 4 else stall_secs * 5
reason = 'no wiki progress for %s s (stalled)' % sys.argv[1]
# Optional hard clock-abort HHMM (e.g. '0230'). Empty/'' = disabled.
abort_at = sys.argv[5] if len(sys.argv) > 5 else ''
def _abort_due():
    # Compare NUMERICALLY as minutes-since-midnight. The abort is ONLY meant to
    # fire when a run has crossed into the early-morning window (00:00..abort_at);
    # a daytime run (e.g. 1813) must NOT compare as '> 0230' just because its
    # digits are larger. So: fire only if now is in [0000, abort_at) — the run has
    # carried over 3am and must yield to the fail-fast nightly extraction.
    if not abort_at:
        return False
    now = 3600 * int(time.strftime('%H')) + 60 * int(time.strftime('%M'))
    ab = 3600 * int(abort_at[:2]) + 60 * int(abort_at[2:4])
    return now < ab  # in the early-morning window before the abort time
last = newest_mtime(root)
started = time.monotonic()
while True:
    time.sleep(60)
    # Clock-abort gate (catch-up only): if we're in the early-morning window
    # (00:00..abort_at), the run has overrun into the nightly zone -> kill NOW
    # and release the lock before the fail-fast 3am nightly extraction.
    # Disabled when abort_at is '' (the nightly run).
    if _abort_due():
        reason = 'hard clock abort at %s' % abort_at
        kill_extractor(parent)
        break
    now = newest_mtime(root)
    if now > last:
        last = now
        started = time.monotonic()   # progress seen -> reset inactivity clock
    if (time.monotonic() - started > stall_secs) or (time.monotonic() >= max_secs):
        kill_extractor(parent)
        break
" "$STALL_SECS" "$$" "$WIKI_DIR" "$WATCHDOG_MAX_SECS" "$ABORT_AT" "$WIKI_LOCK_DIR" "$LOCK_SCRIPT" "$LOCK_NAME" 2>/dev/null &
WATCHDOG_PID=$!
# (kill $WATCHDOG_PID below cancels it on normal completion)

# Release the extraction lock on ANY exit of the extraction invocation (normal
# completion, error, or watchdog SIGKILL of session-to-wiki.py whose own trap
# cannot run). Guarded: we only release when the lock is NOT live-held by a
# different writer, so we never clobber a genuine lock owned by another process
# (e.g. wiki-compact). The FIXED wiki-lock.sh status already removes stale
# (dead-pid / no-pid-old) locks, so release here is idempotent and safe.
release_lock_safe() {
    # status exits 0 = live-held by a real process -> leave it alone.
    if "$LOCK_SCRIPT" status "$LOCK_NAME" >/dev/null 2>&1; then
        return 0
    fi
    # Free, or stale dir already removed by status. Drop anything residual
    # (idempotent) so no dead-pid lock is ever left standing.
    "$LOCK_SCRIPT" release "$LOCK_NAME" >/dev/null 2>&1 || true
    return 0
}
trap 'release_lock_safe' EXIT

# Snapshot durable-progress baselines (git HEAD + .checkpoint) BEFORE extraction.
# Used by the tail classifier below to distinguish "interrupted but productive"
# from a genuine 0-progress stall (anti false-exit-0: judge on durable git/checkpoint
# DELTA, never wall-time). See fusion verdict memento-clean-exit.
WIKI_REPO="${WIKI_DIR:-$HOME/wiki}"
RUN_START_HEAD="$(git -C "$WIKI_REPO" rev-parse HEAD 2>/dev/null || echo none)"
RUN_START_CP="$(( $(wc -l < "$WIKI_REPO/.checkpoint" 2>/dev/null || echo 0) ))"

PY_ARGS=("--auto" "--max" "$MAX_SESSIONS")
if [ -n "$RETRY_SESSION" ]; then
  PY_ARGS+=("--retry-session" "$RETRY_SESSION")
fi
caffeinate -dim /usr/bin/python3 "$SCRIPT_DIR/session-to-wiki.py" "${PY_ARGS[@]}" 2>&1 || EXIT_CODE=$?
# Cancel the watchdog (extraction finished).
kill "$WATCHDOG_PID" 2>/dev/null || true
wait "$WATCHDOG_PID" 2>/dev/null || true
trap - EXIT

# Post-extraction lint check
echo "[wiki-extract-pipeline] Extraction exit code: $EXIT_CODE" >&2
"$SCRIPT_DIR/wiki-lint.sh" 2>&1 || true

# Fused durable-progress classifier (2026-08-29): if the extractor exited non-zero
# (e.g. self-budget watchdog killed it mid-run or an error), but this run made
# durable progress (git HEAD advanced OR .checkpoint grew), then for a MULTI-session
# catch-up (MAX_SESSIONS != 1) report success-with-partial so cron doesn't falsely
# mark the run 'failed'. Never mask the nightly --max 1 (MAX_SESSIONS == 1 stays
# non-zero) and never mask a genuine 0-progress stall. Marker goes to STDERR so
# stdout stays empty (empty-stdout-is-silent -> no false cron/Telegram ping).
if [ "${EXIT_CODE:-0}" -ne 0 ] && [ "${MAX_SESSIONS:-1}" != "1" ]; then
    RUN_NEW_HEAD="$(git -C "$WIKI_REPO" rev-parse HEAD 2>/dev/null || echo none)"
    RUN_NEW_CP="$(( $(wc -l < "$WIKI_REPO/.checkpoint" 2>/dev/null || echo 0) ))"
    RUN_PROGRESS=0
    [ "${RUN_NEW_HEAD}" != "${RUN_START_HEAD}" ] && RUN_PROGRESS=1
    [ "${RUN_NEW_CP}" -gt "${RUN_START_CP}" ] && RUN_PROGRESS=1
    if [ "$RUN_PROGRESS" -eq 1 ]; then
        echo "CATCHUP: INTERRUPTED-AFTER-DURABLE-PROGRESS (HEAD ${RUN_START_HEAD}->${RUN_NEW_HEAD}, .checkpoint ${RUN_START_CP}->${RUN_NEW_CP}). Reporting success-with-partial." >&2
        EXIT_CODE=0
    else
        echo "CATCHUP: STALLED - 0 durable progress this run (exit code ${EXIT_CODE}). Real failure, not masking." >&2
    fi
fi
exit "$EXIT_CODE"