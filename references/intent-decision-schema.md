# Intent & Decision Schema — Implementation Reference

## New Fact Types

| Type | Subdirectory | Description |
|------|-------------|-------------|
| `decision` | `decisions/` | Settled calls with WHY, rejected alternatives, classification |
| `question` | `questions/` | Open/resolved/partial questions |

## Frontmatter Fields

### On ALL types (new)
```yaml
intent: core_goal | supporting_goal | passing_mention
```

### On type=decision (new)
```yaml
decision: "What was actually decided"
why: "Why this was chosen — the reasoning"
rejected_alternatives: ["Alternative A — because rejected for reason X"]
decision_weight: architectural | directional | rule-setting
status: settled | tentative
```

### On type=question (new)
```yaml
question_status: open | resolved | partial
```

## Decision Routing

| Status | Confidence | Target |
|--------|-----------|--------|
| tentative | any | `staging/` |
| settled | high | `decisions/` |
| settled | medium | `decisions/` |
| settled | low | `staging/` |

Priority: status check before confidence check. Tentative decisions always go to staging/ regardless of confidence. Low-confidence settled decisions also go to staging/ — only high/medium confidence settled decisions land in decisions/.

## Auto-Injection

When a settled decision is written (`write_fact()`), `inject_decision_into_linked_pages()` is called. It:

1. For each `related_pages` title, reads the target page
2. Checks if `## Key Decisions` section exists
3. If yes: inserts a new bullet `- [[Decision Title]] — decision text` under the section
4. If no: appends `## Key Decisions` section at end of body
5. Stamps frontmatter (`updated: today`)
6. Skips if the decision wikilink already exists in the body

## Prompt Changes

### CANDIDATE_PROMPT additions
- Extract list now includes `6. QUESTIONS`
- Intent Classification section: core_goal/supporting_goal/passing_mention
- Decision classification: decision_weight + status
- Output format includes `type: decision|question`, `intent`, `decision`, `why`, `rejected_alternatives`, `decision_weight`, `status`, `question_status`

### ENRICH_PROMPT additions
- Intent Classification section at top (same as CANDIDATE)
- Backlinks instruction updated to mention decision pages
- Output format same extensions as CANDIDATE

## Source Code Location

All changes in `~/.hermes/scripts/session-to-wiki.py`:

- `FACT_DIRS` — lines 60-66 (decision + question added)
- `get_existing_pages()` — lines 324-335 (scans decisions/ and questions/)
- `get_existing_pages_summary()` — lines 345+ (same)
- `update_index_with_entries()` — section_map expanded
- `CANDIDATE_PROMPT` — lines 533+ (intent + decision fields)
- `ENRICH_PROMPT` — lines 636+ (intent + decision fields)
- `inject_decision_into_linked_pages()` — lines 943-1000 (new function)
- `write_fact()` — lines 1005+ (decision routing, frontmatter merge, auto-injection)

## Schema File

`~/wiki/SCHEMA.md` — updated with full frontmatter spec for all new types.