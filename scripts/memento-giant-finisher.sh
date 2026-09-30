#!/bin/bash
# memento-giant-finisher.sh — finish parked giants OUTSIDE the 60-min cron wall (PARALLEL).
#
# WHAT: For each session parked in ~/wiki/.diverted-giant (a giant the size-aware
#   estimator refused because its full two-pass extraction exceeds the nightly's
#   50-min window), run it to completion with BYPASS semantics under a 240-min
#   watchdog — no 60-min cron wall applies because this runs detached
#   from a fast kickoff cron, or manually/supervised.
#
# WHY (fused verdict 2026-09-15): the nightly can never fit a >50-min session.
#   The estimator correctly refuses/diverts it, but a parked giant needs a real
#   completion path or it sits forever re-refused each night. This is that path.
#   It uses the EXISTING MEMENTO_ESTIMATOR_BYPASS=1 + --retry-session semantics.
#
# PARALLEL (fused 2026-09-28): drains up to MEMENTO_FINISHER_PARALLEL (default 1,
#   cap 5) DISTINCT parked giants concurrently. The deployed extractor uses a single
#   SHARED `extraction` lock (session-to-wiki.py sets LOCK_NAME='extraction'), NOT a
#   per-sid lock, so N>1 parallel workers just contend on one mutex and N-1 workers
#   fail-fast. PARALLEL therefore defaults to 1 — safe parallel extraction only
#   becomes possible once a per-sid lock (extraction-<sid>) exists. Each worker
#   undiverts its own sid on success, so no cross-worker collision on queue removal.
#   A failed worker does NOT halt the batch.
#
#   NOTE: replaced mapfile (bash 4+, absent on macOS 3.2) with a compatible read loop.
#
# HOW (the targeting invariant): BOTH flags are mandatory per inner invocation:
#   - --retry-session <sid>  narrows the run to EXACTLY that parked sid.
#   - MEMENTO_ESTIMATOR_BYPASS=1  re-admits the parked sid.
#   On success, session-to-wiki.py runs _undivert(sid) on checkpoint, so the
#   .diverted-giant line auto-clears.
#
# CLOCK-YIELD (fusion 2026-09-15): the finisher drains ONLY within
#   [DRAIN_START, YIELD) (default 06:00..09:44) so it never collides with the
#   3am/4am extraction+curation or the 10:00 catch-up. MEMENTO_RESUME=1 makes
#   the yield lossless (resumes per-chunk).
#
# Usage: memento-giant-finisher.sh
# Env:   MEMENTO_WIKI_DIR, MEMENTO_FINISHER_DRAIN_START_MIN/YIELD_MIN/WALL_MIN,
#        MEMENTO_FINISHER_PARALLEL (default 2, cap 5)
set -u
WIKI_DIR="${MEMENTO_WIKI_DIR:-$HOME/wiki}"
DIVERT_FILE="$WIKI_DIR/.diverted-giant"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Clock-yield defaults (minutes since midnight): drain window 06:00..09:44, wall 09:55.
DRAIN_START_MIN="${MEMENTO_FINISHER_DRAIN_START_MIN:-360}"   # 06:00
YIELD_MIN="${MEMENTO_FINISHER_YIELD_MIN:-584}"               # 09:44 (last allowed start)
WALL_MIN="${MEMENTO_FINISHER_WALL_MIN:-595}"                 # 09:55 (watchdog kill wall)

now_min() { echo $(( 10#$(date +%H) * 60 + 10#$(date +%M) )); }

# Finisher semantics: re-admit + disable the refuse branch + no budget curtailment.
export MEMENTO_ESTIMATOR_BYPASS=1
export MEMENTO_MAX_RETRY=999
export MEMENTO_RUN_BUDGET_MIN=0
export MEMENTO_PRE_START_ESTIMATOR="${MEMENTO_PRE_START_ESTIMATOR:-0}"
export MEMENTO_MIN_AGE_MIN=0
export MEMENTO_RESUME=1
# MEMENTO_WATCHDOG_MIN is set PER-ITERATION below (minutes-until-WALL).

[ -s "$DIVERT_FILE" ] || exit 0                                # empty queue -> silent no-op

while [ -s "$DIVERT_FILE" ]; do
  _nm="$(now_min)"
  # Drain window: only 06:00..09:44. Outside -> yield (never 3am/4am, never 10:00 catch-up).
  if [ "$_nm" -lt "$DRAIN_START_MIN" ] || [ "$_nm" -ge "$YIELD_MIN" ]; then
    echo "finisher: outside drain window (${_nm} min; window ${DRAIN_START_MIN}..${YIELD_MIN}); yielding, queue stays parked" >&2
    break
  fi
  # Bound THIS inner run to the 09:55 wall so it can't overrun the 10:00 catch-up.
  export MEMENTO_WATCHDOG_MIN=$(( WALL_MIN - _nm ))

  # Parallel: drain up to PARALLEL distinct parked giants concurrently.
  PARALLEL="${MEMENTO_FINISHER_PARALLEL:-1}"
  [ "$PARALLEL" -gt 5 ] && PARALLEL=5
  [ "$PARALLEL" -gt 1 ] && PARALLEL=1   # serial only (shared extraction lock); parallel unsafe (shared git+lock)
  [ "$PARALLEL" -ge 1 ] || PARALLEL=1

  BATCH=()
  while IFS= read -r _sid; do
    [ -n "$_sid" ] && BATCH+=("$_sid")
  done < <(cut -f1 "$DIVERT_FILE" | awk '!seen[$0]++' | head -n "$PARALLEL")
  [ "${#BATCH[@]}" -gt 0 ] || break

  PIDS=()
  for SID in "${BATCH[@]}"; do
    [ -n "$SID" ] || continue
    echo "finisher: dispatching parked giant $SID (parallel=$PARALLEL)" >&2
    "$SCRIPT_DIR/wiki-extract-pipeline.sh" --retry-session "$SID" &
    PIDS+=("$!")
  done

  # Wait for the whole batch; a failed worker does NOT halt the batch (no HOL blocking).
  for p in "${PIDS[@]}"; do
    wait "$p" || echo "finisher: a worker failed (see .retry/.diverted-giant); continuing" >&2
  done
done
exit 0
