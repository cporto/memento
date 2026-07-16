# Hermes Agent Memory: Two-Layer Design

**Status:** Draft for discussion with Claude  
**Date:** 2026-07-13  
**Context:** Based on Codacus's understory video, Karpathy's LLM Wiki pattern, and Hermes's existing infrastructure

---

## The Core Problem

Memory systems for AI agents have two fundamentally different concerns that get conflated:

1. **Extraction** — pulling facts out of conversations and writing them down
2. **Curation** — keeping the written facts healthy (dedup, contradiction resolution, pruning, cross-linking)

The "I moved to a new city" example from Codacus's video is the perfect stress test. When a user changes where they live, the memory system needs to:
- Detect the contradiction (old city vs. new city)
- **Supersede** the old value in place, not leave two conflicting facts
- Propagate the change through related facts (commute time, local recommendations, nearby services)
- Do it autonomously — the main agent shouldn't have to notice or fix it

A batch extract-and-append system can't do this. But a full daemon is overkill for the M4 Pro's 24GB.

---

## The Two-Layer Design

### Layer 1: Extraction (CLI + Cron)

**What it does:** Pulls facts from Hermes session DB, writes OKF-compatible markdown to `~/wiki/`

**Architecture:**
- `~/.hermes/scripts/session-to-wiki.py` — Python script, cron-triggered
- Reads Hermes SQLite DB for new sessions since last run
- Calls an LLM (local or API) with an extraction prompt
- Writes to `~/wiki/` with confidence gating:
  - `high` → live pages in `entities/` or `concepts/`
  - `medium` → live page, flagged for corroboration
  - `low` → `staging/` directory, manual review
- Heartbeat logging to `log.md` so missed runs are visible

**Where it runs:** Cron on local machine, daily at 6am

**Why it's not enough alone:** Pure append-only. Can't resolve contradictions, can't prune orphans, can't propagate changes. If the user moves cities, it writes "lives in Y" next to "lives in X" and walks away.

---

### Layer 2: Curation (Compaction Cron)

**What it does:** Reads the wiki bundle, finds problems, fixes them

**Architecture:**
- `~/.hermes/scripts/wiki-compact.py` — Python script, less frequent cron (daily or every 2 days)
- Calls a small local LLM (Gemma 4B, Hermes 4-14B on oMLX) with a narrow task:
  - **Orphan detection** — pages with no incoming links → wire into related pages or delete
  - **Broken link detection** — `[[wikilinks]]` that point to nonexistent pages → fix or remove
  - **Contradiction resolution** — two pages saying different things about the same entity → supersede the old value, leave a note
  - **Deduplication** — same fact extracted from multiple sessions → merge, don't duplicate
  - **Cross-reference propagation** — when a fact changes, cascade through related pages

**Where it runs:** Same machine, same cron system. Not a daemon — no always-on overhead.

**The key insight:** This is a *classification* workload, not a *generation* workload. The model reads N pages and answers questions like "is this orphaned?", "do these two facts contradict each other?", "should this link to that?". A small model is sufficient. No need for Claude-level reasoning.

---

## Why Two Layers Instead of One

| Concern | Layer 1 (Extraction) | Layer 2 (Curation) |
|---------|---------------------|-------------------|
| Cadence | Daily | Daily or every 2 days |
| LLM need | Decent model (DeepSeek/Claude) | Small model (Gemma 4B, Hermes 14B) |
| What it does | Write new facts | Maintain existing facts |
| Failure mode | Missing facts, stale wiki | Orphan pages, contradictions |
| RAM cost | Zero (cron, one-shot) | ~2-4GB for 30 seconds |
| Complexity | Python script + DB | Python script + LLM call |

The separation means:
- Extraction can use the best available model (API or local)
- Curation can use the cheapest available model (local oMLX)
- They run on different schedules
- Neither is a daemon — no always-on memory footprint

---

## Relationship to Existing Infrastructure

### Hermes Dreaming (`agent-dreaming-agnostic`)
Already exists. Three-phase memory consolidation that reviews sessions, scores candidates, promotes to memory. But it targets Hermes's built-in memory store (key-value, ~2K chars), not the wiki. It's a complement, not a replacement.

### Understory (Codacus)
Understory wraps both layers into one MCP daemon. It's clean and well-designed, but:
- Requires a Docker container (~200-500MB RAM)
- Needs an LLM provider (or uses your local llama.cpp)
- MCP tool schemas burn context on every turn
- The daemon is always-on, which competes with the local LLM on 24GB

Our CLI + cron approach gets the same effect without the daemon. The trade-off is we lose the real-time MCP interface and the web graph viewer. For a single-agent setup, those aren't worth the RAM cost.

### Karpathy's LLM Wiki
The wiki format (index.md, log.md, wikilinks, entity/concept types) is the foundation. Understory formalizes it via OKF spec. We follow the same format so the wiki is portable — if we ever want to point Obsidian at it or migrate to understory, nothing breaks.

---

## What We're Not Doing (Yet)

- **MCP server for the wiki** — adds daemon overhead, burns context on tool schemas. CLI is more context-efficient for single-agent.
- **understory on TNAS** — gated on RAM measurement. The J3355 + 4GB is tight for SearXNG alone. Understory is Phase 2 if RAM headroom exists.
- **Real-time sync** — one-way rsync from TNAS to local if we deploy understory later. Manual edits go through a separate path to avoid conflicts.
- **Web graph viewer** — nice to have, but Obsidian at `~/wiki/` gives a graph view without any infrastructure.

---

## The Implementation Order

### Phase 0: Validate Foundations
1. [x] Hand-write test wiki page → confirm session-start injection works
2. [ ] Verify Hermes DB schema has what we need (full transcripts, reliable role labels, timestamps)

### Phase 1: Extraction Pipeline
1. Write `session-to-wiki.py` with confidence gating
2. First N runs in dry-run mode (write to staging/)
3. Register cron job with heartbeat logging
4. Tune extraction prompt → promote staging entries

### Phase 2: Curation Pipeline
1. Write `wiki-compact.py` with orphan detection + contradiction resolution
2. Wire into compaction cron (less frequent than extraction)
3. Test with small local model on oMLX
4. Tune: automatic vs. flag-for-review

### Phase 3: Optional — understory on TNAS
1. Measure actual free RAM on TNAS
2. If ≥1.5GB free → deploy understory Docker
3. Register as MCP server in Hermes
4. One-way sync to local wiki

---

## Questions for Claude

1. **Does the two-layer separation make sense?** Extraction vs. curation are different workloads with different cadences and model requirements. Or is the extra complexity not worth it?

2. **Small model for curation — viable?** Can a 4-14B parameter model reliably detect contradictions, orphans, and broken links in a wiki of ~50-200 pages? Or does this need something stronger?

3. **Cron vs. daemon trade-off.** We're trading real-time responsiveness (daemon) for zero always-on RAM cost (cron). Is that the right call for a 24GB personal machine that also runs the local LLM?

4. **What are we missing?** Any edge cases or failure modes in the two-layer design that understory's daemon approach handles better?