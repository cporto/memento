<p align="center">
  <img src="assets/memento-logo.svg" alt="Memento" width="400">
</p>

<p align="center">
  <em>A persistent, cross-session memory system for LLM agents. Named after the Nolan film — a three-layer architecture that turns conversations into a durable, interlinked knowledge base.</em>
</p>

Memento is a **zero-daemon, CLI + cron** memory system for AI agents. It extracts facts from conversation transcripts, writes them to a structured markdown wiki, and keeps the wiki healthy — all without a vector DB, MCP daemon, or cloud API.

Named after the Nolan film — a three-layer architecture that turns conversations into durable, interlinked knowledge. Inspired by [Codacus](https://youtube.com/@Codacus) (Anirban Kar) and his [understory](https://github.com/thecodacus/understory) project, which is the best practical demonstration of persistent agent memory we've seen. The "Enrich Before You Create" and "Link Both Ways" rules come straight from his YouTube walkthrough.

> **Curation note:** The current implementation covers extraction and linting. Semantic curation (contradiction resolution, orphan wiring, deduplication) is a documented future direction — see `references/hermes-memory-plan.md` for the design.

## Architecture

```
╔══════════════════════════════════════════════════════════╗
║                    MEMENTO                                ║
║     neocortex + hippocampus + consolidation               ║
╚══════════════════════════════════════════════════════════╝

┌──────────────────────────────────────────────────────────┐
│ Layer 1: Neocortex (agent memory)                        │
│ Fast, always-on, injected every turn.                    │
│ For: preferences, corrections, environment facts.        │
├──────────────────────────────────────────────────────────┤
│ Layer 2: Hippocampus (wiki at ~/wiki/)                   │
│ Deep, structured, unlimited. Entities, concepts,         │
│ decisions, comparisons, questions.                       │
├──────────────────────────────────────────────────────────┤
│ Layer 3: Consolidation (session-to-wiki pipeline)        │
│ Cron-driven extraction from session DB into wiki.        │
│ The "dreaming" process that consolidates short-term       │
│ into long-term memory.                                   │
└──────────────────────────────────────────────────────────┘
```

## Quick Start

1. **Clone the repo** and symlink the wiki directory:
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
   # Configure your LLM endpoint
   export LLM_API_BASE_URL="http://127.0.0.1:8000/v1"
   export LLM_API_KEY="your-api-key"
   export LLM_MODEL="your-model"

   # Run a test extraction
   python3 memento/scripts/session-to-wiki.py --auto --max 5
   ```

4. **Wire the wiki into your agent's system prompt:**
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
- Calls an LLM (local or API) to extract structured facts
- Writes to a markdown wiki with YAML frontmatter, wikilinks, and backlinks
- Confidence gating: high-confidence → live, low-confidence → staging

### Wiki Health
- `wiki-lint.sh` — checks duplicate slugs, broken [[links]], orphan pages
- `wiki-summary.sh` — generates a compact snapshot for sharing with other agents

### Schema
OKF-compatible markdown with frontmatter:
- **Entities** — people, machines, projects, tools
- **Concepts** — techniques, patterns, workflows
- **Decisions** — with WHY, rejected alternatives, decision weight
- **Comparisons** — side-by-side analyses
- **Questions** — open, resolved, or partial

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

- **Mechanical vs Intelligent:** Scripts handle deterministic work (DB queries, file writes, git); LLMs handle only the intelligence (fact extraction, entity resolution)
- **Enrich Before You Create:** Before writing a new page, check existing pages. If a fact belongs to something that exists, enrich it instead of creating a duplicate
- **Link Both Ways:** Every new page gets backlinks in related existing pages
- **Confidence Gating:** High-confidence → live pages; medium → staging; low → staging with review flag
- **Zero Daemon:** No always-on services, no MCP servers, no vector DBs. Just cron + scripts + git

## Requirements

- Python 3.10+
- PyYAML
- Hermes Agent (or any agent with a SQLite session store matching the schema)
- An LLM endpoint (local or API) accessible via HTTP

## License

MIT — use freely, share widely.

## Inspiration

- [Codacus](https://youtube.com/@Codacus) (Anirban Kar) — the channel that sparked this whole approach
- [understory](https://github.com/thecodacus/understory) — his self-wiring MCP memory daemon. Star it.