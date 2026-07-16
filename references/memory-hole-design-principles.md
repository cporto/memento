# Memory Hole Design Principles

Reference for the memory-hole system's design philosophy, extracted from SYSTEM.md and ONBOARDING.md at `github.com/your-org/memory_hole` (read 2026-07-15).

## Why It Matters to the Wiki Pipeline

The wiki pipeline (`session-knowledge-pipeline`) extracts facts from sessions. The memory-hole system captures **why** — decisions with reasoning, rejected alternatives, and intent. These should be integrated into the extraction prompts.

## Core Principles

### 1. Decisions Must Include WHY
Every captured decision includes:
- **Decision** — what was decided
- **Why** — the reasoning
- **Rejected** — alternatives considered
- **Why rejected** — why ruled out

### 2. Decision Classification
- **Architectural** — affects system structure
- **Directional** — rules out an approach
- **Rule-setting** — creates constraints
Provisional statements ("maybe X") do NOT qualify.

### 3. Open Questions
- `[ ]` new, `[x]` resolved, `[~]` partial
- Nothing disappears — discussed-but-not-decided becomes a new question

### 4. Collection Window
- 2-exchange window after qualifying decision
- Absorbs additional decisions (resets window)
- Auto-triggers update when window reaches 0

### 5. Context Warnings
- 60% → warn (with humor)
- 80% → auto-save without being asked

### 6. Periodic Check-in
- Every 10 exchanges: "Anything worth saving?"
- User confirmation triggers immediate update

### 7. Update Format
- Full replacement blocks, one per changed file
- Destination stated AFTER the block
- Dense structured output — every line is signal
- No preamble, no commentary

### 8. Never Store
Credentials, passwords, financial details, medical info. Context and reasoning only.

## Onboarding Structure

Five rounds, one question at a time:
1. **Identity & Work** — who they are
2. **Re-Explanation Problem** — what they repeat
3. **Tools & Environment** — their stack
4. **Decision-Making Philosophy** — how they ship
5. **The One Thing** — single most important context

## Source Files
- `github.com/your-org/memory_hole/SYSTEM.md` — update trigger logic, decision classification, context warnings
- `github.com/your-org/memory_hole/ONBOARDING.md` — interview structure, WHO_I_AM.md generation
- `github.com/your-org/memory_hole/GUIDE.md` — setup and usage guide