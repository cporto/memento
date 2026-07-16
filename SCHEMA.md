# Wiki Schema

## Domain
Any domain. This wiki system is domain-agnostic — it works for any collection of entities, concepts, decisions, comparisons, and questions.

## Conventions
- File names: lowercase, hyphens, no spaces
- Every wiki page starts with YAML frontmatter (see below)
- Use `[[wikilinks]]` to link between pages (minimum 2 outbound links per page)
- When updating a page, always bump the `updated` date
- Every new page must be added to `index.md` under the correct section
- Every action must be appended to `log.md`
- Provenance: on pages synthesizing 3+ sources, append `^[raw/articles/source.md]` markers per paragraph

## Frontmatter
```yaml
---
title: Page Title
created: YYYY-MM-DD
updated: YYYY-MM-DD
type: entity | concept | comparison | decision | question
tags: [from taxonomy below]
sources: [raw/articles/source-name.md]
# Optional:
confidence: high | medium | low
contested: true
contradictions: [other-page-slug]
# Intent/Decision fields (ALL types):
intent: core_goal | supporting_goal | passing_mention
# Decision-specific (type: decision only):
decision: "The actual decision made"
why: "Why this was chosen"
rejected_alternatives: ["alternative A — because rejected for reason X"]
decision_weight: architectural | directional | rule-setting
status: settled | tentative
# Question-specific (type: question only):
question_status: open | resolved | partial
---
```

## Tag Taxonomy
Customize per domain. Examples:
- **People:** colleague, collaborator, client, mentor
- **Projects:** active, planned, archived, infrastructure
- **AI:** llm, agent, provider, model, memory, tool, integration, deployment
- **Meta:** comparison, timeline, tutorial, review, prediction
- **Decision:** architectural, directional, rule-setting

## Page Thresholds
- Create a page when an entity/concept appears in 2+ sources OR is central to one source
- **Decisions:** extract every qualifying decision with WHY, rejected alternatives, and classification — even if from a single source
- **Questions:** track as long as they remain open or partially resolved
- Don't create pages for passing mentions
- Split pages over 200 lines
- Archive fully superseded content to `_archive/`

## Entity Pages
One page per notable entity: people, companies, products, models.
Include overview, key facts, relationships (wikilinks), sources.

## Concept Pages
One page per concept or topic.
Include definition, current state, open questions, related concepts.

## Decision Pages
One page per decision or decision cluster.
Every decision must include:
- **Decision:** what was decided
- **Why:** reasoning behind the choice
- **Rejected alternatives:** what was considered and rejected, with reasons
- **Decision weight:** architectural (affects system structure), directional (rules out an approach), rule-setting (creates a constraint)
- **Status:** settled or tentative

Tentative decisions go to `staging/` for review. Settled decisions go to `decisions/`.

Decision pages auto-inject a `## Key Decisions` section into the linked entity/concept page.

## Question Pages
One page per open question or question cluster.
- `status: open` — unresolved, still needs a decision
- `status: resolved` — answered, closed
- `status: partial` — partially resolved, still needs more

## Comparison Pages
Side-by-side analyses with table format and verdict/synthesis.

## Intent Classification
Every fact carries an `intent` field:
- `core_goal` — central to the user's purpose
- `supporting_goal` — enables or supports the core goal
- `passing_mention` — mentioned but not actionable

## Update Policy
When new info conflicts with existing content:
1. Check dates — newer generally supersedes older
2. If genuinely contradictory, note both positions with dates and sources
3. Mark contradiction in frontmatter: `contradictions: [page-name]`
4. Flag for user review in lint

## Confidence vs Status
Confidence and status are independent axes:
- **Confidence** (high/medium/low) = extraction quality — how sure are we the LLM got this right
- **Status** (settled/tentative) = decision finality — has the user committed to this or is it still provisional

A low-confidence settled decision is correct (user made the call) but the extraction may be fuzzy. A high-confidence tentative decision is accurately extracted but the user hasn't locked it in yet.