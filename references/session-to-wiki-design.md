# session-to-wiki.py — Design & Decisions

## Architecture: Two-Mode Pipeline

The script separates concerns into two distinct modes that communicate via JSON over pipes:

**Default mode:** Queries Hermes SQLite DB for unprocessed sessions. Prints each session as a JSON
task object to stdout (one per line), followed by a final metadata line with `_checkpoint` array.

**`--ingest` mode:** Reads a JSON array of extracted facts from stdin. Writes OKF markdown pages
with YAML frontmatter. Checkpoints processed sessions. Git-commits the wiki.

This separation allows the extraction LLM to be the best available model (API or local)
while keeping DB querying and file-writing synchronous and crash-safe.

## DB Schema (Hermes ~/.hermes/state.db)

Verified 2026-07-13 — 436 sessions, 28,500 messages, full TEXT transcripts.

```
messages: id, session_id, role (user|assistant|tool), content (TEXT),
          tool_call_id, tool_calls, tool_name, timestamp, token_count,
          finish_reason, reasoning, reasoning_content, reasoning_details,
          compacted (0/1), active (0/1), observed, effect_disposition
sessions: id (TEXT UUID), title (TEXT)
```

Key columns for extraction: `role`, `content`, `active=1`, `compacted=0`.

## Safety Features

1. **Lock acquisition** — `wiki-lock.sh` with atomic mkdir. PID file written for staleness
   detection via `os.kill(pid, 0)`. Stale orphan locks auto-cleaned.
2. **Version pin** — Aborts if Hermes version doesn't contain `0.18` (major.minor check).
3. **Per-session checkpoint** — `~/wiki/.checkpoint` is appended after EACH session completes.
   Crash-safe: if script dies mid-run, next tick skips completed sessions.
4. **Git auto-commit** — Every extraction run produces a descriptive commit.
5. **Content filtering** — SQL-level: `WHERE active=1 AND compacted=0`. Python-level: skip
   sessions with <2 user+assistant messages (noise filter).
6. **STDERR/STDOUT separation** — Logs to stderr, data to stdout. No mixing.

## YAML Frontmatter Safety

**Lesson from Claude review:** Hand-rolled YAML serialization with string interpolation
breaks on titles containing colons (e.g. "Hermes: An Agent"). Always use PyYAML's
`yaml.dump()` for frontmatter:

```python
import yaml
frontmatter = {
    "title": title,
    "created": today,
    "updated": today,
    "type": ftype,
    "tags": tags,
    "confidence": confidence,
    "sources": sources,
}
yaml_block = yaml.dump(frontmatter, default_flow_style=False, allow_unicode=True, sort_keys=False)
file_content = f"---\n{yaml_block}---\n\n# {title}\n\n{summary}\n"
```

**Do NOT** use `default_style='|'` — it produces multiline block scalars that break
frontmatter parsers. **Do NOT** hand-escape colons with `\\:` — that's not valid YAML.

## Index.md Section Awareness

The `update_index_with_entries()` function now organizes entries into the correct type sections
(`## Entities`, `## Concepts`, `## Comparisons`). It finds the section header, finds the next
section boundary, and inserts the new entry between them. Falls back to appending at the bottom
if the section doesn't exist.

`write_fact()` returns `(title, ftype)` tuple — the ftype is used by the index updater
to determine which section to place the entry under.

## Content Truncation

Messages longer than 2000 characters are truncated to the **last 500 characters** in the
extraction task JSON. This is intentional — the tail of a long message is usually the
most relevant (conclusion/decision). Full content is preserved in the DB for reprocessing.

## Edge Cases Handled

- Duplicate titles: checked against filesystem before writing (case-insensitive)
- Empty LLM response: graceful degradation with WARN log
- Bad JSON from LLM: salvage attempt (`f"[{raw_input}]"`)
- Missing WIKI_CHECKPOINT env var: defaults to `[]` (no checkpointing, idempotent)
- Lock acquired but script crashes: stale PID detection on next run
- Non-UTF8 files in wiki: `read_text(encoding="utf-8", errors="replace")`
- Pathological long titles: capped to 80 chars via `safe_filename()`