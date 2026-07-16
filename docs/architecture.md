# Architecture

## Three-Layer Design

Memento is inspired by the human brain's memory system:

### Layer 1: Neocortex (Agent Memory)
- Fast, always-on, injected every turn
- ~2K character capacity
- For: preferences, corrections, environment facts
- Implemented as the agent's built-in memory tool

### Layer 2: Hippocampus (Wiki)
- Deep, structured, unlimited
- Markdown files with YAML frontmatter
- Organized into: entities, concepts, decisions, comparisons, questions
- For: durable knowledge, cross-session facts, decisions with reasoning

### Layer 3: Consolidation (Pipeline)
- Cron-driven extraction from session DB into wiki
- 2-pass, 2-call-per-pass design
- The "dreaming" process that consolidates short-term into long-term memory

## Extraction Pipeline

### Two-Call-per-Pass Design

Each extraction pass makes 2 LLM calls:

1. **Call 1 (Candidate Naming):** Sends transcript + wiki index (titles + summaries). The LLM returns facts with a `candidate_page` field — the title of an existing wiki page the fact might belong to, or `null` if genuinely new.

2. **Call 2 (Enrichment Decision):** For each unique `candidate_page` title, fetches the full page content from disk. Sends transcript + full page bodies + the candidate facts. The LLM decides enrich vs create with the actual content visible.

### Flow

```
For each session:
  1. Pass 1, Call 1:  Candidate naming against wiki index
  2. Pass 1, Call 2:  Enrichment decision with full candidate page content
     -> write pass 1 output -> git commit
  3. Pass 2, Call 1:  Candidate naming against updated wiki (pass 1 committed)
  4. Pass 2, Call 2:  Enrichment decision with full candidate page content
     -> write pass 2 output -> git commit -> checkpoint
```

### Confidence Gating

| Confidence | New Page | Enrichment |
|------------|----------|------------|
| high | Live page | Patch live page |
| medium | Live + flag | Staging/ |
| low | Staging/ | Staging/ |

## Key Design Principles

### Mechanical vs Intelligent
Scripts handle deterministic work (DB queries, file writes, git commits). LLMs handle only the intelligence (fact extraction, entity resolution, enrichment decisions).

### Enrich Before You Create
Before writing a new wiki page, search existing pages. If the fact belongs to something that already exists, patch that page instead of creating a new one.

### Link Both Ways
When you genuinely create a new page, go back and add backlinks in related existing pages. This is enforced mechanically (Python code) rather than by prompt instruction.

### Structured Fields Over Prose-Policing
The LLM reliably ignores prose instructions about formatting. The fix: add a required `related_pages` JSON field. Python code then injects `[[wikilinks]]` syntax into the summary mechanically and appends backlinks to target pages.

## Safety Infrastructure

- **Lock file:** `wiki-lock.sh` with atomic `mkdir` for mutual exclusion between extraction and curation cron jobs
- **Version pin:** Script aborts if Hermes version doesn't match expected
- **Per-session checkpoint:** Flat `.checkpoint` file, append-only. Crash-safe
- **Git auto-commit:** Every extraction run produces a descriptive commit
- **Git-based crash recovery:** `git checkout -- .` + `git clean -fd` to revert uncommitted state from a prior crash