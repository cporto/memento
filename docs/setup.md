# Setup Guide

## Prerequisites

- Python 3.10+
- PyYAML (`pip install pyyaml`)
- An LLM endpoint accessible via HTTP (OpenAI-compatible API)
- An agent with a SQLite session store (Hermes Agent or compatible)

## Installation

1. Clone the repo:
   ```bash
   git clone https://github.com/your-org/memento ~/memento
   ```

2. Link the wiki directory:
   ```bash
   ln -s ~/memento/wiki ~/wiki
   ```

3. Install Python dependencies:
   ```bash
   pip install pyyaml
   ```

4. Make scripts executable:
   ```bash
   chmod +x ~/memento/scripts/*.sh
   ```

## Configuration

### LLM Endpoint

Set environment variables for the extraction LLM:

```bash
export LLM_API_BASE_URL="http://127.0.0.1:8000/v1"  # Local oMLX
export LLM_API_KEY="your-api-key"
export LLM_MODEL="Qwen3.5-9B-MLX-4bit"
```

### Hermes Session Injection

In your agent's system prompt, add:

```
SESSION START: read ~/wiki/index.md in full before any substantive reply.
Read last 15 lines of ~/wiki/log.md for recent changes.

WIKI EDITS: before creating a page, check ~/wiki/ for existing pages.
After creating or editing: (1) add to index.md section, (2) append one line to log.md.
```

## First Run

Test the extraction pipeline:

```bash
cd ~/memento/scripts
python3 session-to-wiki.py --auto --max 5
```

This will process 5 unprocessed sessions, extract facts, and write them to `~/wiki/`.

## Cron Setup

See `docs/cron-setup.md` for scheduling the extraction pipeline.

## Seed Pages

Before running automated extraction, hand-write 5-10 core entity pages in `~/wiki/entities/` and `~/wiki/concepts/`. This gives the LLM real enrichment targets from the very first session.