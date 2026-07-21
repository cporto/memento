# Memento v6 — session-to-wiki.py rewrite

Theme: **failed and empty are different things.** Every serious v5 bug traced back to conflating "the LLM found nothing" with "the LLM call broke," then checkpointing as if it succeeded.

## Data-loss fixes

**1. Failure vs empty semantics.** `call_llm_for_extraction` now returns `None` on any failure (network, HTTP, parse error, truncated output) and `[]` only when the model genuinely found no facts. v5 returned `[]` for both, and `--auto` checkpointed unconditionally — so a session processed during an API outage was permanently marked done with zero facts. Sessions are now checkpointed only when every executed pass succeeded. Failures record into `.retry` with an attempt counter; after `MEMENTO_MAX_RETRY` (default 5) attempts the session is checkpointed with a loud `FAILED-PERMANENT` entry in `log.md` so it stops blocking the queue but never disappears silently.

**2. Pass-aware dedup.** v5's same-session dedup checked `source_session in fm["sources"]`. Pass 1 stamps the session into sources on every page it touches, so pass 2 — the enrichment pass, the whole point of the two-pass design — was silently blocked from appending to any page pass 1 had created or touched, while still counting as "enriched" in the stats. Dedup is now an in-process set keyed by `(page, session, pass)`. Content-level dedup is unchanged and still protects `--reprocess` across runs.

**3. Active-session guard.** Sessions with activity in the last `MEMENTO_MIN_AGE_MIN` minutes (default 120) are skipped *without* checkpointing. v5 would checkpoint an in-progress conversation, permanently freezing extraction at whatever the cron run happened to see. Degrades gracefully with a one-time warning if the messages table has no recognizable timestamp column.

**4. `--ingest` repaired.** Extract mode emits checkpoint entries as dicts; v5's ingest unpacked them as tuples (crash), and had it run, would have attributed *every* fact to the first session of the run. Ingest now parses both formats and attributes each fact from its own `sources[]`. Bad JSON on stdin is a hard failure (exit 2, nothing checkpointed) — v5's salvage-wrap could "succeed" on a fragment and drop the rest.

**5. Slug-collision safety.** The existence check was keyed by title but files are keyed by slug, so two different titles with the same slug ("oMLX Config" / "oMLX: config!") silently overwrote each other. Colliding creates now write to a hash-suffixed filename with a loud warning to review and merge.

**6. Truncated LLM output is a failure.** `finish_reason == "length"` returns `None` instead of parsing a cut-off array. `max_tokens` raised to 8192 and configurable via `LLM_MAX_TOKENS`.

## Design fixes

**7. Confidence gating on the enrichment path.** v5 gated only at page creation; a low-confidence fact whose title matched a live page edited the live page directly. Low-confidence facts aimed at live pages now divert to `staging/<slug>--pending.md`. Page confidence can only be lowered (min of existing and incoming) — a later high-confidence fact can no longer launder a medium-confidence page upward, and one weak fact no longer overwrites a strong page's rating arbitrarily.

**8. Staging gets an exit.** `--promote` lists the staging inventory with ages, promotes one slug or all to their live directories (per `type:` frontmatter), bumps confidence to medium, settles tentative decisions, and updates the index. The heartbeat now reports staging count and oldest age so the backlog is visible in `log.md` every run.

**9. Checkpoint query.** `NOT IN (?,?,...)` with one placeholder per processed session would eventually exceed SQLite's variable limit (999 on older builds) and start throwing. Filtered in Python instead.

**10. Unified truncation + chunking.** v5 had two contradictory truncation policies (auto: first 10k chars; extract: last 500 chars of anything over 2000 — so a 2001-char message kept less than a 2000-char one). One shared head+tail policy now, plus session-level chunking under `MEMENTO_TRANSCRIPT_BUDGET` (default 60k chars). This is the structural fix for the oMLX context-ceiling silent skip: long sessions extract per-chunk instead of blowing the local model's window, and per-page dedup absorbs cross-chunk overlap.

**11. No candidate-facts fallback.** When the enrichment call failed, v5 wrote the raw candidate facts. Candidates lack `body_action`/`replaces` and haven't seen full page content — writing them is exactly the duplicate-creation path the 2-call design prevents. Retrying next run is cheap; wrong writes are expensive to unwind.

## Safety & hygiene

**12. Injection hardening.** Transcripts are wrapped in explicit `UNTRUSTED DATA` sentinels and both prompts instruct the model to treat everything inside as data, never instructions. Every executed `body_action=replace` is audited to `log.md` with old→new snippets, so an LLM-driven overwrite of live content is reviewable in git history rather than only in cron stderr.

**13. Secret redaction.** Common credential patterns (OpenAI/Anthropic/GitHub keys, AWS access keys, Slack tokens, bearer tokens, private-key headers, generic `api_key=...`) are scrubbed from summaries before anything is written. The wiki is git-tracked; a leaked secret would otherwise persist in history forever.

**14. Personal data out of the public prompts.** v5's prompts hardcoded the author's real hostnames, machine specs, and config paths as examples — leaking personal infrastructure in a public repo and biasing extraction for anyone else adopting the tool. Examples are now domain-neutral; personal alias knowledge loads at runtime from `MEMENTO_HINTS_FILE` (`~/.memento/hints.md`), injected into both prompts. The "related_pages" instruction also now requires genuine semantic relationships — the old example taught the model to pad the list with unrelated pages.

**15. Sentinel blocks for mechanical edits.** Backlinks consolidate into one `<!-- backlinks:start/end -->` block (legacy loose lines are migrated in), and Key Decisions injection lives in `<!-- key-decisions:start/end -->`. Both are idempotent on re-runs; v5 stacked a new `Backlinks:` line per link and found the decisions section by string search.

**16. Lock moved off `/tmp`.** macOS periodically purges `/tmp`, which could drop the lock mid-backfill and admit a second writer. Root is now `~/.cache/wiki-locks` (`WIKI_LOCK_DIR` to override — both the Python and `wiki-lock.sh` honor it). The lock-dir-exists-but-no-PID-file case (crash between mkdir and pid write) no longer deadlocks: dirs older than an hour with no PID are treated as stale. `wiki-lock.sh` also rejects path traversal in lock names.

**17. Version-check heartbeat.** v5 wrote its version-mismatch entry to `log.md` before holding the lock — breaching its own mutual-exclusion invariant. Failure status now goes to `.health`, a single lock-free beacon file agents can read at session start.

## Cost

**18. Conditional pass 2.** Pass 2's only job is enriching against pages pass 1 just created; when pass 1 created nothing, the index is unchanged and pass 2 re-derives pass 1's output at double the cost. It's now skipped in that case (`MEMENTO_ALWAYS_PASS2=1` to force). On steady-state incremental runs this halves LLM calls per session.

## Observability

- Heartbeats report `OK`/`PARTIAL` with failed-session IDs, plus staging count and age.
- `.health` beacon updated on every run outcome, including crashes.
- Exit codes: 0 ok, 1 fatal/lock, 2 ingest failed, 3 partial — cron can alert on non-zero.
- New env knobs documented in `--help`.

## Minor bug fixes

- `update_index_with_entries` compared `(title, ftype)` tuples against a set of title strings — the pre-filter never filtered; it only worked via the secondary in-content check. Fixed.
- Dead code removed from the old `add_backlinks` (abandoned `page_stamped` refactor, double file read); all frontmatter stamping consolidated into `stamp_frontmatter()`.
- YAML parse errors in page frontmatter no longer crash the index summary builder.
- `MEMENTO_WIKI_DIR` / `MEMENTO_DB_PATH` env overrides added (also what makes the test suite possible).

## Testing

`test_v6.py`: 35 functional assertions against a scratch wiki covering slug collisions, pass-aware dedup, confidence gating and the min-only policy, replace + audit trail, sentinel idempotency and legacy migration, index dedup, chunking/truncation, secret redaction, retry lifecycle, ingest attribution, promotion, and the lock script. No LLM or Hermes DB required.

## Migration notes

- Delete any stale `/tmp/wiki-locks` after switching.
- Existing pages with loose `Backlinks:` lines migrate automatically on their next touch.
- `.retry` and `.health` appear in the wiki dir; add both to `.gitignore` alongside `.checkpoint` if it's tracked.
- Create `~/.memento/hints.md` with your machine-alias knowledge (hostname ↔ hardware mappings) — that content intentionally no longer ships in the prompts.
