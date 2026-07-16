---
name: memory-hole-system
description: "Zero-infrastructure LLM memory system — personal knowledge management via markdown rules, profile, and per-project docs."
version: 1.1.0
author: Original author
platforms: [macos, linux]
metadata:
  hermes:
    tags: [memory, knowledge-management, note-taking, personal-kb]
    related_skills: [obsidian]
---

# Memory Hole System

> **Superseded by Memento (2026-07-16):** Memory-hole was the precursor to the Memento wiki system at `~/wiki/`. Its content was imported into the wiki on 2026-07-15 as seed data. This document remains as a reference for the original design principles (decision capture with WHY, rejected alternatives, open questions) that informed the Memento schema expansion.

## What It Is

A zero-infrastructure LLM memory system — no vector DB, no API, no daemon. Just markdown files in a git repo that serve as the agent's memory and the user's project knowledge base.

The core insight: **LLM memory doesn't need infrastructure.** The agent reads files on startup, writes decisions as they're made. The repo IS the memory.

## Where It Lives

- **Local:** `~/Documents/GitHub/memory-hole/`
- **Repo:** `github.com/your-org/memory-hole` (private)

## File Structure (template)
## File Structure (template)

```
memory-hole/
├── START_MEMORY_HOLE.md    # System rules and activation instructions
├── WHO_I_AM.md             # User profile — who they are, how they work
├── Memory_Hole.md           # Event-based update trigger rules
└── projects/               # One file per project, updated as work progresses
    ├── project-name-1.md
    └── ...
```

### START_MEMORY_HOLE.md

The entry point. Contains system rules for interacting with the memory hole:
- Respect user preferences
- Don't re-explain decisions already logged
- Instructions on how and when to use the system

### WHO_I_AM.md

The user's self-description — personality, communication style, work habits, values. It's what the user wrote about themselves, not what the agent inferred.

### Memory_Hole.md

The trigger document. Contains rules for WHEN to update each project doc. Not a changelog — a ruleset that says "if X happens, update file Y."

### projects/

One markdown file per active project. Tracking:
- Current status
- Key decisions made
- Architecture notes
- Links to relevant repos/docs
- Next steps

## How It Works (for the Agent)

### When you start a session

Before beginning work, check if the memory hole is active:

1. Check if the repo path exists: `~/Documents/GitHub/memory-hole/`
2. Read `START_MEMORY_HOLE.md` for system rules
3. Read `WHO_I_AM.md` for user profile
4. Check `Memory_Hole.md` for update trigger rules
5. Browse `projects/` to see what's active

### Trigger: "umh"

When the user says **"umh"** (or "update memory hole"), update the relevant file(s) in the memory hole:
- Log the decision or milestone
- Update project status
- Record architecture changes
- Add new links/references

The trigger is the user's shorthand for "commit this to persistent knowledge."

### When to update on your own initiative

- After a significant decision was made (model choice, architecture direction, tool selection)
- After completing a project phase
- When you discover something the user would want remembered
- When you've done 5+ tool calls of progress on a project

### Style for entries

- Concise, factual
- What was decided, not the full discussion
- One decision per bullet or section
- Include dates where relevant
- Link to GitHub repos, file paths, or external URLs

## For the User

The memory hole is a **living document system**. It doesn't get stale because:
- Files are markdown — easy to edit manually
- The agent updates them during work
- Git tracks history if you want to see what changed
- No special tools, syncing, or infrastructure needed

## See Also

- `github.com/your-org/memory-hole` — Private repo with personal knowledge base
- The local copy has the actual content the agent reads
- The local copy has the actual content the agent reads
