<p align="center">
  <img src="assets/memento-logo.svg" alt="Memento" width="400">
</p>

<p align="center">
  <em>Hermes Agent memory that works across sessions. Three layers, no magic.</em>
</p>

Memento is a memory system for [Hermes Agent](https://hermes-agent.nousresearch.com). It reads your past conversations, picks out the important facts, and stores them in a markdown wiki that Hermes can read on every new chat. No vector databases, no always-on servers, no cloud API calls — it runs on scripts and cron jobs.

Named after the Nolan film. Inspired by [Codacus](https://youtube.com/@Codacus) (Anirban Kar) and his [understory](https://github.com/thecodacus/understory) project — the best practical demo of persistent agent memory I've seen. The "Enrich Before You Create" and "Link Both Ways" rules are lifted straight from his YouTube walkthrough.

> **Current status:** v7.1 — extraction pipeline with slug registry (prevents duplicates across directories), staging auto-lifecycle (auto-promote at ≥3 sources, auto-purge at >14 days), and enrichment-first write path. Full semantic curation (contradiction resolution, orphan wiring, cross-reference propagation) is next — see `references/hermes-memory-plan.md`.

## Architecture

```
╔═══════════════════════════════════════════════════╗
║                     MEMENTO                       ║
║     short-term → thinking → long-term memory      ║
╚═══════════════════════════════════════════════════╝

┌───────────────────────────────────────────────────┐
│ Layer 1: Injected context (every turn)             │
│ Always-on, no thinking required.                   │
│ Stores: preferences, corrections, environment      │
├───────────────────────────────────────────────────┤
│ Layer 2: Wiki at ~/wiki/                           │
│ Deep, unlimited.                                   │
│ Stores: entities, concepts, decisions,             │
│ comparisons, open questions                        │
├───────────────────────────────────────────────────┤
│ Layer 3: Session-to-wiki pipeline (cron job)       │
│ Runs daily. Reads session DB, extracts facts,      │
│ writes wiki pages. The "overnight consolidation"   │
│ that turns conversations into durable knowledge.   │
└───────────────────────────────────────────────────┘
```

## Quick Start

1. **Clone the repo** (you need Hermes Agent — the pipeline reads its SQLite DB):
   ```bash
   git clone https://github.com/your-org/memento ~/memento
   ln -s ~/memento/wiki ~/wiki
   ```

2. **Install dependencies:**
   ```bash
   pip install pyyaml
   chmod +x memento/scripts/*.sh
   ```

3. **Set up the extraction pipeline:**
   ```bash
   # Point it at your LLM (local or API)
   export LLM_API_BASE_URL="http://127.0.0.1:8000/v1"
   export LLM_API_KEY="your-api-key"
   export LLM_MODEL="your-model"

   # Test it out — pulls facts from your last 5 sessions
   python3 memento/scripts/session-to-wiki.py --auto --max 5
   ```

4. **Tell Hermes to read the wiki:**
   ```
   SESSION START: read ~/wiki/index.md in full before any substantive reply.
   Read last 15 lines of ~/wiki/log.md for recent changes.
   ```

5. **Schedule the cron job:**
   ```bash
   # Daily extraction at 6am using the shell wrapper
   # (see docs/cron-setup.md for details)
   ```

## What It Does

### Extraction Pipeline
- Reads session transcripts from the Hermes SQLite DB
- Calls an LLM (local or API) to pull out structured facts
- Writes them to a markdown wiki with YAML frontmatter, wikilinks, and backlinks
- High-confidence facts go to live pages; low-confidence goes to staging for review

### Wiki Health
- `wiki-lint.sh` — checks for duplicate slugs, broken [[links]], and orphan pages
- `wiki-summary.sh` — generates a compact snapshot for sharing with other agents

### Schema
Markdown with frontmatter, split into categories:
- **Entities** — people, machines, projects, tools
- **Concepts** — techniques, patterns, workflows
- **Decisions** — including WHY, what you rejected, and how confident you were
- **Comparisons** — side-by-side analysis of options
- **Questions** — open, resolved, or partially answered

## Project Structure

```
memento/
├── README.md                          # This file
├── LICENSE                            # MIT
├── SCHEMA.md                          # Wiki schema and conventions
├── scripts/
│   ├── session-to-wiki.py             # Main extraction pipeline (1792 lines)
│   ├── wiki-lock.sh                   # Mutual exclusion lock
│   ├── wiki-lint.sh                   # Wiki health checker
│   ├── wiki-summary.sh                # Claude snapshot generator
│   ├── wiki-extract-pipeline.sh       # Cron wrapper script
│   └── run-extraction-test.py         # Model comparison test harness
├── docs/
│   ├── setup.md                       # Installation and configuration
│   ├── architecture.md                # Full architecture breakdown
│   ├── extraction-model.md            # Model selection guide
│   └── cron-setup.md                  # Cron job configuration
├── references/                        # Design docs and analysis
│   ├── hermes-memory-plan.md
│   ├── session-to-wiki-design.md
│   ├── understory-analysis.md
│   ├── claude-review-reality-check.md
│   └── memory-hole-design-principles.md
└── wiki/                              # Template wiki (empty)
    ├── .gitignore
    ├── index.md
    ├── log.md
    ├── entities/
    ├── concepts/
    ├── decisions/
    ├── comparisons/
    ├── questions/
    ├── staging/
    ├── raw/articles/
    └── .trash/
```

## Design Principles

- **Mechanical vs Intelligent:** Scripts handle the boring stuff (querying the DB, writing files, git); LLMs do the brain work (extracting facts, figuring out what's related)
- **Enrich Before You Create:** Before writing a new page, check if one already exists. If your fact fits, add to it instead of making a duplicate
- **Link Both Ways:** Every new page gets backlinks in the existing pages it relates to
- **Confidence Gating:** High-confidence facts go to live pages; medium goes to staging; low goes to staging with a review flag
- **Zero Daemon:** No always-on services, no MCP servers, no vector DBs. Just cron, scripts, and git

## Requirements

- Python 3.10+
- PyYAML
- [Hermes Agent](https://hermes-agent.nousresearch.com) — the pipeline reads `~/.hermes/state.db` directly
- An LLM endpoint (local or API) accessible via HTTP

> **Not a universal tool.** The extraction pipeline reads Hermes Agent's SQLite session database. If you run another agent, the concepts and wiki schema are portable but the pipeline needs a different DB adapter.

## License

MIT — use it, share it.

## Inspiration

- [Codacus](https://youtube.com/@Codacus) (Anirban Kar) — the channel that sparked this whole approach
- [understory](https://github.com/thecodacus/understory) — his self-wiring MCP memory daemon