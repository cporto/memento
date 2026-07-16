# Claude Sonnet 5 Review: Memory Architecture Reality Check

**Date:** 2026-07-13  
**Model:** Claude Sonnet 5 (pinned via delegation config)  
**For:** a user's Hermes two-layer memory system  

---

## The Single Linchpin

> **That Hermes session transcripts actually contain enough clean, attributable, fact-bearing signal to extract from.**

Everything else — two-layer split, confidence gating, contradiction resolution, small-model curation — is downstream tooling built on top of "the raw material is good." If sessions are mostly tool-command exchanges rather than natural conversation, if role labels are unreliable, or if personal facts just don't show up in what gets logged, the entire architecture curates an empty or garbage wiki.

**Verdict after verification:** ✅ Confirmed. 436 sessions, 28,500 messages, full-quality transcripts with reliable role labels. The linchpin holds.

---

## Critical Preconditions (Must Be True)

| # | Precondition | Status |
|---|-------------|--------|
| 1 | DB has full transcripts, not summaries | ✅ Text stored in `messages.content` |
| 2 | Role labels are correct (user vs. assistant) | ✅ 2,733 user, 12,792 assistant, 12,845 tool |
| 3 | Enough sessions have substantive fact-bearing content | ✅ Months of personal/design/research chats |
| 4 | DB schema is stable or version-checked | ⚠️ Script needs version pin |
| 5 | Extraction LLM produces structurally correct output | ❓ Untested |
| 6 | Entities disambiguate cleanly | ❓ Strategy documented, untested |
| 7 | Wiki stays small enough (~50-200 pages) for small-model curation | ✅ Currently 3 pages |
| 8 | Cron heartbeat actually checked | ⚠️ Pattern documented, not wired yet |
| 9 | Git rollback from day one | ✅ Set up |
| 10 | Human reviews staging/ occasionally | ❓ No cadence yet |

---

## Three-Layer Design (Refined from Claude's Review)

Claude recommended splitting the original "Layer 2: Curation" into two separate passes:

### Pass 1: Extraction (best-available model)
Pull facts from DB, write OKF markdown with confidence gating.

### Pass 2: Deterministic Lint (Python, no LLM)
- Parse `[[wikilinks]]`, build inbound-link map
- Check file existence for every wikilink
- Validate YAML frontmatter
- Check index.md completeness
- No model needed — 20 lines of Python

### Pass 3: Semantic Curation (small local LLM)
- Resolve contradictions (pre-filtered candidate pairs, not O(n²))
- Merge duplicates
- Wire orphans into related pages
- Cross-reference propagation: flag for regeneration, DON'T generate

---

## Key Risk: Contradiction Resolution Misfire

"I moved to Portland" vs. "I'm in Portland this week for a conference" look identical to a same-day, low-context model doing classification. One should supersede a home-city fact, the other absolutely should not.

**Mitigations needed:**
- Run curation in shadow/dry-run mode against real data first
- Manual-check its calls before it touches live wiki
- Git auto-commit enables rollback
- Superseded facts go to `.trash/`, not deleted
- Log every change in `log.md` for audit

---

## Small Model for Curation: Partial Yes

A 4-14B model can classify contradictions *if* you:
1. Pre-filter candidate pairs (group by shared entity/tag first)
2. Only compare pages within a group (not all pairs)
3. Narrow the task to "do these two specific values conflict?" — not "find all conflicts in the wiki"

Build a small eval set (10-20 synthetic wiki states) before trusting it with in-place edits.

---

## References

- Original plan: `~/hermes-memory-plan.md`
- Claude's full review: this session's subagent output (`deleg_99d10b73`)
- Reality check subagent output (`deleg_56e36f22`)
- Understory analysis: `references/understory-analysis.md`