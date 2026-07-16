# Understory — Architecture Analysis

**Source:** https://github.com/thecodacus/understory (commit history as of 2026-07-13)
**Author:** Codacus (Anirban Kar)
**Reviewed for:** CLI+cron memory architecture comparison

---

## What Understory Is

A self-wiring, plain-markdown memory layer for AI agents. Exposes MCP tools
(memory_query, memory_add, memory_update, memory_status, memory_maintain)
over stdio or streamable HTTP. Based on OKF v0.1 spec (Open Knowledge Format).

## Key Architectural Decisions

### 1. Design Rule: Conformance in Code, Not Prompts

The deterministic bundle layer validates frontmatter (`type` required),
regenerates `index.md` files, appends `log.md` entries (newest-first, spec §7),
and sandboxes all paths to the bundle root. The LLM decides *what* to change;
the code guarantees the result is a conformant bundle.

This is a good pattern — our wiki scripts should follow the same principle.

### 2. Three Access Paths

| Path | What |
|------|------|
| MCP server | memory_query/add/update/status/maintain over stdio or HTTP |
| Web UI | Bundle browser + force-directed graph viewer + agent chat |
| CLI | `pnpm agent:query "..."` / `pnpm agent:mutate "..."` |

### 3. Seed Memory Injection (Critical Pattern)

At session start, the server injects a compact overview of the KB through
two channels:
1. MCP initialize `instructions` field (clients put it in system prompt)
2. `memory_query` tool description (universal fallback)

After writes in a long-lived session, tool descriptions refresh via
`tools/list_changed`. This means the session sees its own writes immediately.

**Equivalent in our system:** `agent.system_prompt` loading `index.md` and
`log.md`. We don't have a `tools/list_changed` equivalent — writes take
effect on the next session only.

### 4. Graph Health & Maintenance

Two mechanisms:

- **Write-time linking** — new knowledge either enriches an existing concept
  (attribute patched in, not filed separately) or creates a new one *and*
  back-links from related concepts. Contradictions are superseded in place.

- **`memory_maintain`** — deterministic lint (orphans + broken links)
  drives an internal agent to wire orphans into related concepts and fix
  dangling links. No-op when graph is healthy.

**Equivalent in our system:** The compaction cron in Layer 2. We don't have
write-time linking (our extraction is batch, not real-time), so our compaction
runs must be more thorough to compensate.

### 5. Stack

| Package | What |
|---------|------|
| `packages/core` | OKF bundle layer (zero LLM) + agent (Vercel AI SDK tool loop) + provider registry |
| `packages/server` | Express: MCP streamable-HTTP, stdio bin, REST API, streaming chat |
| `packages/web` | Vite + React + TS + Tailwind: bundle browser + agent chat |

### 6. Provider Support

- Anthropic (default)
- OpenRouter
- llamacpp (llama-server / llama-swap, model auto-discovered)
- local (any OpenAI-compatible endpoint)

llamacpp integration prefers the **loaded** model so a query doesn't trigger
a multi-minute model swap. This is important for our use case if we ever
deploy understory alongside oMLX.

## Key Differences From Our Approach

| Aspect | Understory | Our CLI+Cron |
|--------|------------|--------------|
| Real-time | Yes — MCP tools write immediately | No — batch via cron |
| Seed injection | Per-session, regenerated dynamically | Static system_prompt |
| Write acknowledgment | tools/list_changed mid-session | Next session reads updated index |
| Agent loop | Internal (Vercel AI SDK tool loop) | External (Hermes agent uses terminal) |
| RAM footprint | ~200-500MB as daemon | Zero (transient cron) |
| Bundle sync | git auto-commit | rsync or file copy |
| Graph UI | Built-in force-directed | Obsidian at ~/wiki/ |

## What We Should Adopt

1. **Conformance-in-code pattern** — our scripts should validate frontmatter
   and regenerate index.md programmatically, not via LLM prompt.

2. **Supersede-in-place for contradictions** — the compaction cron should
   replace old values, not add conflicting ones alongside.

3. **Log.md newest-first** — understory's spec §7 says newest entries at the
   top. We should match this for consistency.

4. **Staging/review path** — understory doesn't have one (it writes directly
   into the live bundle). Our confidence-gating + staging/ is actually more
   conservative.

## What We Should Skip

1. **Daemon** — not worth 200-500MB always-on for single-agent setup
2. **Web graph viewer** — Obsidian provides this for free
3. **MCP tool schemas** — burn context tokens; CLI is cheaper for local-only
4. **Traces** — understory logs every query path; our heartbeat logging is sufficient

## Reference

- OKF v0.1 spec: https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md
- Codacus YouTube: https://www.youtube.com/@Codacus
- Understory GitHub: https://github.com/thecodacus/understory
- Karpathy LLM Wiki gist: https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f