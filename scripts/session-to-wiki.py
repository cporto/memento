#!/usr/bin/env python3
"""session-to-wiki.py — Extract durable facts from Hermes sessions to OKF wiki pages.

Modes:
  default:   Query new sessions, print JSON tasks to stdout.
  --ingest:  Read extracted JSON from stdin, write wiki pages, checkpoint sessions.
  --auto:    Full pipeline — extract, call LLM API, ingest. One-shot.
  --reprocess: Re-extract previously checkpointed sessions (WARNING: use only for
               one-time backfill, not in cron).

Safety:
  - Acquires lock via wiki-lock.sh (mutual exclusion with curation)
  - Version-checks Hermes before touching DB
  - Git-based dirty-tree recovery at startup (reverts uncommitted changes)
  - Checkpoints after each session (crash-safe)
  - ~/wiki/ under git with auto-commit
  - Stale/compacted messages filtered out

Design (v5 fix plan):
  - 2 passes per session (not 3): pass 1 creates, pass 2 enriches
  - 2-call design per pass: call 1 names candidates via index, call 2 enriches with full page content
  - Codacus "enrich before you create" + "link both ways" rules in extraction prompt
  - Per-session incremental writes: pass 1 -> write -> pass 2 -> write -> commit -> checkpoint
  - Tracking enriched (not just created) pages in index/log
  - staging/ directory for low-confidence facts, gitignored
  - body_action field: LLM signals "append" vs "replace" for targeted enrichment
  - Shared touch_page() for all page writes: frontmatter always stamped
"""

import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

# -- Configuration ----------------------------------------------------------
HOME = Path.home()
WIKI_DIR = HOME / "wiki"
DB_PATH = HOME / ".hermes" / "state.db"
CHECKPOINT = WIKI_DIR / ".checkpoint"
LOCK_SCRIPT = Path(__file__).resolve().parent / "wiki-lock.sh"
LOCK_NAME = "extraction"
EXPECTED_HERMES_VERSION = "0.18"  # Prefix match: allows 0.18.x, rejects 0.19+
MIN_MESSAGES = 2  # Skip sessions with fewer user/assistant messages
TAG_TAXONOMY = {"memory", "mcp", "okf", "knowledge-base", "llm", "tooling",
                "workflow", "hardware", "software", "preference", "person",
                "project", "tool", "technique", "pattern", "decision",
                "comparison", "concept", "entity", "ai", "development"}

FACT_DIRS = {
    "entity": "entities",
    "concept": "concepts",
    "comparison": "comparisons",
    "decision": "decisions",
    "question": "questions",
}

LOG_FILE = WIKI_DIR / "log.md"
INDEX_FILE = WIKI_DIR / "index.md"


# -- Logging -----------------------------------------------------------------

def log(msg: str, level: str = "INFO"):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{ts}] [{level}] {msg}", file=sys.stderr)


def abort(msg: str):
    log(msg, "FATAL")
    sys.exit(1)


# -- Subprocess helper -------------------------------------------------------

def run_cmd(cmd: list, check: bool = True, timeout: int = 30) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, check=check, timeout=timeout)
    except FileNotFoundError:
        abort(f"Command not found: {cmd[0]}. Is it on PATH?")
    except subprocess.TimeoutExpired:
        abort(f"Command timed out after {timeout}s: {' '.join(cmd)}")
    except subprocess.CalledProcessError as e:
        abort(f"Command failed (exit {e.returncode}): {' '.join(cmd)}\n{e.stderr.strip()}")


# -- Safety: version check, lock, git, dirty-tree recovery --------------------

def version_check():
    """Abort if Hermes version doesn't match expected."""
    result = run_cmd(["hermes", "--version"])
    if EXPECTED_HERMES_VERSION not in result.stdout:
        # Write a distinguishable heartbeat log entry so the failure is visible
        # at session start, not just in cron stderr.
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        try:
            append_log(
                f"## [{today}] session-to-wiki | VERSION MISMATCH -- "
                f"expected {EXPECTED_HERMES_VERSION}*, got: {result.stdout.strip()}"
            )
        except Exception:
            pass  # Don't mask the abort with a logging error
        abort(
            f"Hermes version mismatch: expected {EXPECTED_HERMES_VERSION}*, got: "
            f"{result.stdout.strip()}"
        )
    log(f"Hermes version OK")


def acquire_lock() -> bool:
    """Returns True if lock acquired."""
    result = subprocess.run(
        [str(LOCK_SCRIPT), "acquire", LOCK_NAME],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        # Write PID for staleness detection
        pid_file = Path(f"/tmp/wiki-locks/{LOCK_NAME}/pid")
        pid_file.write_text(str(os.getpid()))
        log("Lock acquired")
        return True
    # Check staleness
    pid_file = Path(f"/tmp/wiki-locks/{LOCK_NAME}/pid")
    if pid_file.exists():
        try:
            old_pid = int(pid_file.read_text().strip())
            # Check if process is alive via kill -0
            try:
                os.kill(old_pid, 0)
            except OSError:
                log(f"Stale lock from dead PID {old_pid}, removing", "WARN")
                subprocess.run([str(LOCK_SCRIPT), "release", LOCK_NAME])
                # Retry acquire
                result = subprocess.run(
                    [str(LOCK_SCRIPT), "acquire", LOCK_NAME],
                    capture_output=True, text=True
                )
                if result.returncode == 0:
                    Path(f"/tmp/wiki-locks/{LOCK_NAME}/pid").write_text(str(os.getpid()))
                    log("Lock acquired (after stale cleanup)")
                    return True
        except (ValueError, OSError) as e:
            log(f"Could not check PID staleness: {e}", "WARN")
            # Remove stale lock dir directly
            import shutil
            shutil.rmtree(f"/tmp/wiki-locks/{LOCK_NAME}", ignore_errors=True)
    log("Cannot acquire lock: stale lock present", "ERROR")
    return False


def release_lock():
    run_cmd([str(LOCK_SCRIPT), "release", LOCK_NAME], check=False)
    log("Lock released")


def git_commit(message: str):
    """Auto-commit wiki changes. No-op if nothing changed."""
    try:
        run_cmd(["git", "-C", str(WIKI_DIR), "add", "-A"])
        result = run_cmd(["git", "-C", str(WIKI_DIR), "diff", "--cached", "--quiet"], check=False)
        if result.returncode != 0:
            run_cmd(["git", "-C", str(WIKI_DIR), "commit", "-m", message])
            log(f"Git committed: {message}")
        else:
            log("No changes to commit")
    except Exception as e:
        log(f"Git error (non-fatal): {e}", "WARN")


def recover_dirty_tree():
    """Revert uncommitted wiki changes caused by a crash mid-session.

    Uses git to detect a dirty working tree (uncommitted changes from a prior
    crash), then reverts them. Cannot touch content from other sessions --
    prior sessions' work is already safely committed.

    Must be called AFTER acquiring the lock (mutual exclusion with curation).
    Excludes staging/ from git clean so pending human review is not wiped.
    """
    try:
        result = run_cmd(
            ["git", "-C", str(WIKI_DIR), "status", "--porcelain"],
            check=False, timeout=10
        )
        if not result.stdout.strip():
            log("Tree is clean -- no crash recovery needed")
            return

        dirty_count = len(result.stdout.strip().splitlines())
        log(f"Tree is dirty ({dirty_count} uncommitted changes) -- recovering from crash", "WARN")

        # Revert modified tracked files (undoes pass 2's enrichment edits cleanly)
        run_cmd(["git", "-C", str(WIKI_DIR), "checkout", "--", "."], check=False, timeout=10)
        log("Reverted modified tracked files")

        # Remove untracked files but preserve staging/ (gitignored, human review)
        run_cmd(
            ["git", "-C", str(WIKI_DIR), "clean", "-fd", "-e", "staging/"],
            check=False, timeout=10
        )
        log("Removed untracked files (preserved staging/)")

        # Confirm tree is clean now
        result2 = run_cmd(
            ["git", "-C", str(WIKI_DIR), "status", "--porcelain"],
            check=False, timeout=10
        )
        if result2.stdout.strip():
            log(f"Tree still dirty after recovery: {result2.stdout.strip()[:200]}", "WARN")
        else:
            log("Crash recovery complete -- tree is clean")

    except Exception as e:
        log(f"Crash recovery error (non-fatal): {e}", "WARN")


# -- Wiki file helpers -------------------------------------------------------

def load_checkpoint() -> set:
    if not CHECKPOINT.exists():
        return set()
    with open(CHECKPOINT) as f:
        return {line.strip() for line in f if line.strip()}


def save_checkpoint(session_id: str):
    with open(CHECKPOINT, "a") as f:
        f.write(f"{session_id}\n")


def safe_filename(title: str) -> str:
    """Convert title to lowercase kebab-case filename, max 80 chars."""
    name = title.lower().strip()
    name = re.sub(r"[^a-z0-9\s-]", "", name)
    name = re.sub(r"[\s-]+", "-", name)
    return name.strip("-")[:80] or "untitled"


def slug(text: str) -> str:
    return safe_filename(text)


def read_index_entries() -> set:
    """Read existing index entries."""
    if not INDEX_FILE.exists():
        return set()
    entries = set()
    for line in INDEX_FILE.read_text().splitlines():
        line = line.strip()
        if line.startswith("- [["):
            m = re.match(r"- \[\[([^\]]+)\]\]", line)
            if m:
                entries.add(m.group(1))
    return entries


def update_index_with_entries(entries: list):
    """Add new entries to index.md, organizing by type section.

    entries: list of (title, ftype) tuples for NEW pages only.
    """
    existing = read_index_entries()
    new_entries = [e for e in entries if e not in existing]
    if not new_entries:
        return

    if not INDEX_FILE.exists():
        INDEX_FILE.write_text("# Wiki Index\n\n> Content catalog. Every wiki page listed under its type with a one-line summary.\n\n")

    content = INDEX_FILE.read_text()

    for title, ftype in new_entries:
        entry_line = f"- [[{title}]]"
        if entry_line in content:
            continue

        # Find the right section header
        section_map = {"entity": "## Entities", "concept": "## Concepts", "comparison": "## Comparisons", "decision": "## Decisions", "question": "## Questions"}
        section = section_map.get(ftype, "## Concepts")

        # Try to insert after the section header
        section_pos = content.find(f"\n{section}\n")
        if section_pos >= 0:
            # Find the end of this section (next ## or EOF)
            rest = content[section_pos + len(section) + 2:]
            next_section = rest.find("\n## ")
            if next_section >= 0:
                insert_pos = section_pos + len(section) + 2 + next_section
                content = content[:insert_pos] + f"{entry_line}\n" + content[insert_pos:]
            else:
                content += f"{entry_line}\n"
        else:
            # Section doesn't exist yet -- add it
            content += f"\n{section}\n{entry_line}\n"

    INDEX_FILE.write_text(content)
    log(f"Index updated: {len(new_entries)} new entries")


def append_log(line: str):
    """Append entry to log.md (newest first)."""
    if not LOG_FILE.exists():
        LOG_FILE.write_text("# Wiki Log\n\n> Chronological record of all wiki actions. Append-only.\n\n")

    content = LOG_FILE.read_text()
    header_end = content.find("\n\n", content.find("# Wiki Log"))
    if header_end == -1:
        content += f"\n{line}\n"
    else:
        content = content[:header_end+2] + f"{line}\n\n" + content[header_end+2:]
    LOG_FILE.write_text(content)


def get_existing_pages() -> dict:
    """Return {title_lower: Path} for all wiki pages."""
    pages = {}
    for subdir in ["entities", "concepts", "comparisons", "decisions", "questions", "staging"]:
        d = WIKI_DIR / subdir
        if not d.exists():
            continue
        for f in sorted(d.iterdir()):
            if f.suffix == ".md":
                content = f.read_text(encoding="utf-8", errors="replace")
                m = re.search(r"^title:\s*(.+)$", content, re.MULTILINE)
                if m:
                    pages[m.group(1).strip().lower()] = f
    return pages


def get_existing_pages_summary() -> str:
    """Return a compact summary of existing wiki pages (title + first paragraph).

    Used for wiki index injection into the extraction prompt. This is the
    scalable approach: titles + one-line summaries, not full page bodies.
    Includes type, tags, and the first substantive sentence for matching.
    """
    pages = []
    for subdir in ["entities", "concepts", "comparisons", "decisions", "questions", "staging"]:
        d = WIKI_DIR / subdir
        if not d.exists():
            continue
        for f in sorted(d.iterdir()):
            if f.suffix == ".md":
                content = f.read_text(encoding="utf-8", errors="replace")
                m = re.search(r"^title:\s*(.+)$", content, re.MULTILINE)
                if m:
                    title = m.group(1).strip()
                    # Extract type and tags from frontmatter
                    fm_match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
                    tags_str = ""
                    type_str = ""
                    if fm_match:
                        fm = yaml.safe_load(fm_match.group(1))
                        if isinstance(fm, dict):
                            tags = fm.get("tags", [])
                            if isinstance(tags, list):
                                tags_str = f" [{', '.join(tags[:5])}]"
                            type_str = f" ({fm.get('type', '?')})"
                    # Extract first substantive sentence from body
                    body = content.split("---", 2)[-1].strip() if content.count("---") >= 2 else content
                    # Skip the heading line, get the first real sentence
                    body_lines = body.split("\n")
                    first_sentence = ""
                    for line in body_lines:
                        line = line.strip()
                        if line and not line.startswith("#"):
                            first_sentence = line[:200]
                            break
                    pages.append((title, first_sentence, tags_str, type_str))

    if not pages:
        return "No existing wiki pages yet."

    parts = []
    for title, summary, tags_str, type_str in pages:
        parts.append(f"- [[{title}]]{type_str}{tags_str} -- {summary}")
    return "\n".join(parts)


def resolve_page_for_title(title: str, existing_pages: dict) -> Optional[Path]:
    """Find the existing page path for a title (case-insensitive)."""
    title_lower = title.lower().strip()
    return existing_pages.get(title_lower)


def fetch_page_content(title: str, existing_pages: dict) -> Optional[str]:
    """Fetch the full text content of a wiki page by title.

    Returns the entire page content (frontmatter + body), or None if not found.
    Used for the 2-call design: after naming candidates, fetch full content.
    """
    page_path = resolve_page_for_title(title, existing_pages)
    if page_path and page_path.exists():
        return page_path.read_text(encoding="utf-8", errors="replace")
    return None


def parse_page_content(content: str):
    """Parse a wiki page into (frontmatter_dict, body_text).

    Returns (fm_dict, body_str) where body_str is the content after the heading.
    If no frontmatter, returns ({}, full_content).
    """
    fm_match = re.match(r"^---\n(.*?)\n---\n", content, re.DOTALL)
    if fm_match:
        fm = yaml.safe_load(fm_match.group(1))
        if not isinstance(fm, dict):
            fm = {}
        body = content[fm_match.end():]
    else:
        fm = {}
        body = content
    return fm, body


def rebuild_page_content(frontmatter: dict, body: str) -> str:
    """Reassemble a page from frontmatter dict and body text."""
    yaml_block = yaml.dump(frontmatter, default_flow_style=False, allow_unicode=True, sort_keys=False).strip()
    return f"---\n{yaml_block}\n---\n\n{body.strip()}\n"


# -- Shared touch_page(): ALL page writes go through this --------------------

def touch_page(filepath: Path, new_pages: dict, source_session: str = None, touch_fm: dict = None):
    """Touch an existing page: write new content AND stamp frontmatter.

    This is the single consolidated write path for:
      - Enrichment (new content replacing/adding to the body)
      - Backlink append (adding a backlink line)

    new_pages: dict of {title_lower: Path} — updated as pages are written
    source_session: session ID to add to sources (if new facts from this session)
    touch_fm: frontmatter overrides to merge. Used to pass:
        - "updated": today's date (always stamped)
        - "sources": merged with existing (deduped)
        - "tags": merged with existing (deduped)
        Other fields left as-is.
    """
    old_content = filepath.read_text(encoding="utf-8", errors="replace")
    fm, old_body = parse_page_content(old_content)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Always update timestamp
    fm["updated"] = today

    # Merge passed frontmatter overrides
    if touch_fm:
        for key, val in touch_fm.items():
            if key in ("sources", "tags"):
                existing_list = fm.get(key, [])
                if not isinstance(existing_list, list):
                    existing_list = [existing_list] if existing_list else []
                new_list = val if isinstance(val, list) else [val] if val else []
                fm[key] = list(dict.fromkeys(existing_list + new_list))
            else:
                fm[key] = val

    # Add source session if provided
    if source_session:
        existing_sources = fm.get("sources", [])
        if not isinstance(existing_sources, list):
            existing_sources = [existing_sources] if existing_sources else []
        if source_session not in existing_sources:
            fm["sources"] = existing_sources + [source_session]

    new_content = rebuild_page_content(fm, old_body)
    filepath.write_text(new_content)
    log(f"Touched page: {filepath.relative_to(WIKI_DIR)}")


def add_backlinks(page_title: str, related_titles: list, existing_pages: dict, source_session: str = None):
    """Add backlinks to related pages for a newly created or enriched page.

    Uses touch_page() so frontmatter (updated:, sources:) is always stamped.
    """
    for rp_title in related_titles:
        if not rp_title or not rp_title.strip():
            continue
        rp_lower = rp_title.strip().lower()
        rp_path = existing_pages.get(rp_lower)
        if not rp_path:
            continue

        rp_content = rp_path.read_text(encoding="utf-8", errors="replace")
        backlink_entry = f"\n\nBacklinks: [[{page_title}]]"
        if backlink_entry in rp_content:
            continue

        page_stamped = False
        # We need to append the backlink AND stamp frontmatter.
        # Strategy: strip the backlink marker, append it to the body, re-stamp via touch_page
        # but touch_page rebuilds frontmatter -> body, so we just modify body directly
        # before calling touch_page.

        # Remove old backlink reference to this page if it existed in a different position
        old_rp_content = rp_path.read_text(encoding="utf-8", errors="replace")
        fm, body = parse_page_content(old_rp_content)

        # Check if there's already a Backlinks section
        bl_match = re.search(rf"\nBacklinks: \[\[{re.escape(page_title)}\]\]", body)
        if bl_match:
            continue  # Already linked

        new_body = body + backlink_entry

        # Rebuild with fresh frontmatter stamp
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        fm["updated"] = today
        if source_session:
            existing_sources = fm.get("sources", [])
            if not isinstance(existing_sources, list):
                existing_sources = [existing_sources] if existing_sources else []
            if source_session not in existing_sources:
                fm["sources"] = existing_sources + [source_session]

        new_content = rebuild_page_content(fm, new_body)
        rp_path.write_text(new_content)
        log(f"Backlink added to: {rp_path.relative_to(WIKI_DIR)} from [[{page_title}]]")


# -- Extraction prompts ------------------------------------------------------

CANDIDATE_PROMPT = """You are a knowledge extraction agent. Given a conversation transcript, extract durable facts AND identify which existing wiki pages each fact might relate to.

Extract:
1. ENTITIES -- People, projects, tools, services with durable relevance
2. CONCEPTS -- Techniques, patterns, principles, workflows
3. DECISIONS -- Settled calls: what was chosen, what was rejected, why. CRITICAL: every decision MUST include WHY, rejected alternatives, and WHY rejected.
4. PREFERENCES -- User habits, conventions, env details, corrections
5. COMPARISONS -- Trade-offs analyzed, side-by-side evaluations
6. QUESTIONS -- Open questions, unresolved decisions, things that need to be decided later

IMPORTANT: Entity disambiguation. If the transcript mentions "Alex" or "Portland" or anything ambiguous, include enough context in the summary to distinguish this from other references. If you're not sure two references are the same entity, extract them separately with a note.

IMPORTANT -- Intent Classification:
Every fact carries an `intent` field indicating its importance:
- "core_goal" -- central to the user's purpose or long-term direction
- "supporting_goal" -- enables or supports the core goal
- "passing_mention" -- mentioned but not actionable

For decisions specifically, also classify:
- decision_weight: "architectural" (affects how the system is built), "directional" (rules out an approach), or "rule-setting" (creates a constraint)
- status: "settled" (user committed) or "tentative" (still provisional)

Skip: greetings, one-off queries, chit-chat, transient state.

=== Codacus Rule ===
Before writing a new wiki page, check the existing wiki pages listed below.
If this fact belongs to something that already exists, set `candidate_page` to
that page's title so the follow-up stage can fetch its full content and decide
whether to enrich or create. If the fact is genuinely new (no existing page matches),
set `candidate_page` to null.

IMPORTANT -- Match Broadly, Not Just by Title:
- The same entity can be mentioned by different names. "Mac Mini M4" IS the same
  machine as "gpu-box" or "workstation-01". "oMLX config fix" belongs to the "oMLX" page.
  "Hermes-4-14B-4bit" is a model loaded by oMLX -- set candidate_page to "oMLX".
  "Gemma 4" is a model that could run on oMLX -- set candidate_page to "oMLX".
- If the session mentions a topic that has a wiki page about it, set
  candidate_page to that page's title. If the session ADDS new information
  about an existing topic, set candidate_page to that topic.
- If the session CORRECTS information about an existing page (e.g. says
  "Mac Mini M4 not MacBook Air"), set candidate_page to the page that needs
  correction ("gpu-box" or "workstation-01").
- Only set candidate_page to null if the fact is genuinely about a new topic
  that has NO existing wiki page at all.

=== Existing Wiki Pages ===
{wiki_summary}

=== Output Format ===
For each extraction, output a JSON object with these fields:
{{
  "type": "entity|concept|comparison|decision|question",
  "title": "Short Descriptive Title",
  "summary": "2-4 sentences explaining the fact and why it matters.",
  "tags": ["memory", "tooling", "preference"],
  "sources": ["<session_id>"],
  "confidence": "high|medium|low",
  "candidate_page": "Title of existing wiki page this belongs to, or null if genuinely new",
  "related_pages": ["List of 0-3 existing wiki page titles this fact is related to, or empty array if none. REQUIRED field. Must be titles from the Existing Wiki Pages list above."],
  "intent": "core_goal|supporting_goal|passing_mention",
  # For type=decision only:
  "decision": "What was actually decided",
  "why": "Why this was chosen — the reasoning",
  "rejected_alternatives": ["Alternative A — because rejected for reason X"],
  "decision_weight": "architectural|directional|rule-setting",
  "status": "settled|tentative",
  # For type=question only:
  "question_status": "open|resolved|partial"
}}

- high: multiple strong signals, clear evidence in transcript
- medium: plausible but could be wrong -- needs corroboration
- low: speculative, incomplete, or single weak mention

Output ONLY a JSON array, nothing else. One array entry per fact. If no facts found, output an empty array [].

Example:
[
  {{
    "type": "entity",
    "title": "Portland office move",
    "summary": "The user mentioned relocating to Portland for work.",
    "tags": ["decision"],
    "sources": ["abc123"],
    "confidence": "high",
    "candidate_page": null,
    "related_pages": ["gpu-box", "workstation-01"]
  }},
  {{
    "type": "entity",
    "title": "Hermes Agent",
    "summary": "AI assistant by Nous Research. Config uses YAML under ~/.hermes/config.yaml. Connects to local LLM via oMLX.",
    "tags": ["tooling", "software"],
    "sources": ["abc123"],
    "confidence": "high",
    "candidate_page": "Hermes Agent",
    "related_pages": ["gpu-box", "oMLX"]
  }}
]"""


ENRICH_PROMPT = """You are a knowledge enrichment agent. Given a conversation transcript, extract durable facts while considering the FULL content of existing wiki pages.

=== Intent Classification ===
Every fact carries an `intent` field:
- "core_goal" -- central to the user's purpose or long-term direction
- "supporting_goal" -- enables or supports the core goal
- "passing_mention" -- mentioned but not actionable

For decisions specifically, also classify:
- decision_weight: "architectural" (affects how the system is built), "directional" (rules out an approach), or "rule-setting" (creates a constraint)
- status: "settled" (user committed) or "tentative" (still provisional)

=== Codacus Rules ===
RULE 1 -- Enrich before you create:
You have been given the FULL content of existing wiki pages that may relate to facts in this transcript. For each fact, read the existing page content carefully. If the fact belongs to an existing page:
- Set `title` to that page's exact title (matching case)
- The fact replaces/overwrites contradictory content in that page
- The fact's summary is the UPDATED version of the page content, not an appendage

CRITICAL -- Alias/Identity Matching:
A single physical entity may have different names across pages. "Mac Mini M4" IS the same machine as "gpu-box" (its hostname). "Mac Mini M4 Pro 24GB" IS the same machine as "workstation-01". These are aliases, not different entities. If the session describes a machine by its specs (chassis + RAM) and an existing page describes the same specs, TREAT THEM AS THE SAME ENTITY. The existing page's title wins. Do not create a new page for "Mac Mini M4" when "gpu-box" already exists and describes the same Mac Mini M4 16GB.

CRITICAL -- body_action Field:
You MUST set `body_action` to one of:
  "append"  -- The new facts don't contradict existing content. Add them as a new section/paragraph.
  "replace" -- The session CORRECTS or CONTRADICTS specific existing content on the page. Replace only the affected text, not the whole page.

When body_action is "replace", you MUST also set `replaces` to the EXACT old text from the page body that should be replaced. The code will find `replaces` in the body and swap it with your `summary` content. This is a targeted replacement -- only the matched text changes.

Examples of when to use replace:
- Page says "Lives in NYC" but session says "moved to Portland" -> replaces = "Lives in NYC"
- Page says "MacBook Air" but session says "Mac Mini M4 16GB" -> replaces = the exact text that says MacBook Air
- Page has an outdated fact like "uses Claude 3" but session says "switched to DeepSeek V4" -> replaces = the specific sentence/line

Do NOT use replace for adding new facts that don't conflict -- use append for that.
Do NOT set replaces to text that doesn't actually exist in the page body.

CRITICAL -- Backlinks Required on Every New Page:
Every new page (entity, concept, comparison, decision) MUST include [[wikilinks]] in its
summary that point to at least 1-2 existing related pages. For example:
- A new "Gemma 4" entity page should have [[oMLX]] in its summary
- A new "Mac Mini M4" page should have [[gpu-box]] or [[workstation-01]] in its summary
- A new concept page should link to the entity page it describes
- A new decision page should link to the entity/concept it affects

Do NOT create a new page for something that already has a page with content about this topic.
If you are unsure whether a fact belongs to existing page A or page B, choose the more
general page and enrich it, adding the fact as a section or bullet point.

RULE 2 -- Link both ways:
When you genuinely create a new page, include [[wikilinks]] in the summary to
connect to at least 1-2 related existing pages. Every new page MUST have wikilinks.

=== Contradiction Handling ===
If the session says something that contradicts an existing page, the enrichment
OVERWRITES -- do not append alongside. If the page says "Lives in NYC" and the
session says "moved to Portland", the enriched summary (and your `replaces` field)
contains the corrected version. The old content is replaced, not preserved.

=== Existing Wiki Pages (Full Content) ===
Below is the full content of existing wiki pages that may relate to this session:

{full_page_content}

=== Session Transcript ===
## Session: {session_title}
## Session ID: {session_id}

{transcript}

=== Output Format ===
For each extraction, output a JSON object with these fields:
{{
  "type": "entity|concept|comparison|decision|question",
  "title": "Short Descriptive Title",
  "summary": "2-4 sentences explaining the fact and why it matters.",
  "tags": ["memory", "tooling", "preference"],
  "sources": ["<session_id>"],
  "confidence": "high|medium|low",
  "body_action": "append|replace",
  "replaces": "Exact old text to replace in the page body (ONLY if body_action is 'replace', otherwise omit or set to null)",
  "related_pages": ["List of 0-3 existing wiki page titles this fact is related to, or empty array if none. REQUIRED field."],
  "intent": "core_goal|supporting_goal|passing_mention",
  # For type=decision only:
  "decision": "What was actually decided",
  "why": "Why this was chosen — the reasoning",
  "rejected_alternatives": ["Alternative A — because rejected for reason X"],
  "decision_weight": "architectural|directional|rule-setting",
  "status": "settled|tentative",
  # For type=question only:
  "question_status": "open|resolved|partial"
}}

Output ONLY a JSON array, nothing else. One array entry per fact. If no facts found, output an empty array [].

Example (append):
[
  {{
    "type": "entity",
    "title": "Hermes Agent",
    "summary": "AI assistant by Nous Research. Now uses DeepSeek V4 Flash via OpenRouter for main tasks. Config at ~/.hermes/config.yaml. Connects to [[gpu-box]] for local LLM via [[oMLX]].",
    "tags": ["tooling", "software"],
    "sources": ["abc123"],
    "confidence": "high",
    "body_action": "append",
    "replaces": null,
    "related_pages": ["gpu-box", "oMLX"]
  }}
]

Example (replace):
[
  {{
    "type": "entity",
    "title": "gpu-box",
    "summary": "Mac Mini M4, 16GB. Headless inference server.",
    "tags": ["hardware"],
    "sources": ["abc123"],
    "confidence": "high",
    "body_action": "replace",
    "replaces": "MacBook Air, 16GB. Headless inference server.",
    "related_pages": ["workstation-01", "oMLX"]
  }}
]"""


# -- LLM call ----------------------------------------------------------------

def call_llm_for_extraction(prompt: str, session_id: str) -> list[dict]:
    """Call an LLM to extract structured facts from a session transcript.

    Reads config from env vars: LLM_API_BASE_URL, LLM_API_KEY, LLM_MODEL.
    """
    base_url = os.environ.get("LLM_API_BASE_URL")
    api_key  = os.environ.get("LLM_API_KEY")
    model    = os.environ.get("LLM_MODEL")

    if not base_url:
        log(f"[{session_id}] LLM_API_BASE_URL not set", "ERROR")
        return []
    if not model:
        log(f"[{session_id}] LLM_MODEL not set", "ERROR")
        return []

    base_url = base_url.rstrip("/")
    url = f"{base_url}/chat/completions"

    allow_thinking = os.environ.get("LLM_ALLOW_THINKING", "").lower() in ("1", "true", "yes")
    body_dict = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a knowledge extraction agent. Return ONLY a JSON array of facts, nothing else."},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.1,
        "max_tokens": 4096,
    }
    if not allow_thinking:
        body_dict["chat_template_kwargs"] = {"enable_thinking": False}
    body = json.dumps(body_dict).encode("utf-8")

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    max_retries = 3
    last_error = None

    for attempt in range(1, max_retries + 2):
        try:
            req = urllib.request.Request(url, data=body, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=120) as resp:
                raw = resp.read()

            if not raw or not raw.strip():
                log(f"[{session_id}] Empty response from LLM", "ERROR")
                return []

            data = json.loads(raw)
            content = data["choices"][0]["message"]["content"]
            if not content or not content.strip():
                log(f"[{session_id}] Empty content in LLM response", "ERROR")
                return []

            # Strip markdown fences
            cleaned = content.strip()
            if cleaned.startswith("```"):
                first_nl = cleaned.find("\n")
                if first_nl != -1:
                    cleaned = cleaned[first_nl:].strip()
                if cleaned.endswith("```"):
                    cleaned = cleaned[:-3].strip()

            facts = json.loads(cleaned)
            if not isinstance(facts, list):
                log(f"[{session_id}] LLM response not a JSON array", "ERROR")
                return []

            # Validate and normalize related_pages, inject [[wikilinks]] into summaries
            for fact in facts:
                if not isinstance(fact, dict):
                    continue
                rp = fact.get("related_pages")
                if rp is None or not isinstance(rp, list):
                    log(f"[{session_id}] Invalid 'related_pages' in fact \"{fact.get('title', '?')}\"", "WARN")
                    fact["related_pages"] = []
                else:
                    rp[:] = [str(t).strip() for t in rp if str(t).strip()]

                # Validate body_action
                ba = fact.get("body_action", "append")
                if ba not in ("append", "replace"):
                    log(f"[{session_id}] Invalid body_action '{ba}' in fact \"{fact.get('title', '?')}\"", "WARN")
                    fact["body_action"] = "append"

                # Validate replaces
                if fact.get("body_action") == "replace":
                    replaces = fact.get("replaces")
                    if not replaces or not isinstance(replaces, str) or not replaces.strip():
                        log(f"[{session_id}] body_action='replace' but 'replaces' is missing/empty in fact \"{fact.get('title', '?')}\"", "WARN")
                        fact["body_action"] = "append"
                        fact["replaces"] = None

                # Inject [[wikilinks]] into summary based on related_pages
                summary = fact.get("summary", "")
                if summary and fact.get("related_pages"):
                    for rp_title in fact["related_pages"]:
                        wikilink = f"[[{rp_title}]]"
                        if wikilink not in summary:
                            fact["summary"] = summary + f" {wikilink}"
                            summary = fact["summary"]

            return facts

        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                if attempt == 1:
                    log(f"[{session_id}] Rate limited (429) -- retrying after 5s", "WARN")
                    time.sleep(5)
                    continue
                log(f"[{session_id}] Rate limited (429) again -- giving up", "ERROR")
                return []
            elif 400 <= exc.code < 500:
                snippet = exc.read().decode("utf-8", errors="replace")[:200]
                log(f"[{session_id}] HTTP {exc.code}: {snippet}", "ERROR")
                return []
            last_error = exc
            log(f"[{session_id}] HTTP {exc.code} (attempt {attempt})", "WARN")

        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
            log(f"[{session_id}] Network error (attempt {attempt}): {exc}", "WARN")

        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            log(f"[{session_id}] LLM response parse error: {exc}", "ERROR")
            return []

        if attempt <= max_retries:
            time.sleep(min(2 ** attempt, 30))

    log(f"[{session_id}] LLM call failed after {max_retries} retries: {last_error}", "ERROR")
    return []


# -- Prompt builders ---------------------------------------------------------

def build_candidate_prompt(session_id: str, title: str, transcript: str, pass_num: int = 1) -> str:
    """Build the candidate-naming prompt (call 1 of the 2-call design).

    Injects the wiki index (titles + summaries) and asks the LLM to name
    candidate existing pages each fact might belong to.
    """
    wiki_summary = get_existing_pages_summary()

    prompt = (
        f"## Session: {title}\n"
        f"## Session ID: {session_id}\n"
        f"## Pass: {pass_num}/2 -- Candidate Naming\n\n"
        f"{transcript}\n\n"
        f"---\n\n"
        f"{CANDIDATE_PROMPT.replace('<session_id>', session_id).replace('{wiki_summary}', wiki_summary)}"
    )
    return prompt


def build_enrich_prompt(session_id: str, title: str, transcript: str,
                        candidate_facts: list[dict], existing_pages: dict,
                        pass_num: int = 1) -> str:
    """Build the enrichment prompt (call 2 of the 2-call design).

    Fetches full page content for all unique candidate pages named in the
    first call, then asks the LLM to decide enrich vs create with full context.
    """
    # Collect unique candidate page titles
    candidate_titles = set()
    for fact in candidate_facts:
        cp = fact.get("candidate_page")
        if cp and isinstance(cp, str) and cp.strip():
            candidate_titles.add(cp.strip())

    # Fetch full content for each candidate page
    full_page_content_parts = []
    for cp_title in sorted(candidate_titles):
        content = fetch_page_content(cp_title, existing_pages)
        if content:
            full_page_content_parts.append(f"--- Page: {cp_title} ---\n{content}")
        else:
            full_page_content_parts.append(f"--- Page: {cp_title} ---\n[Content not found or page does not exist]")

    if not full_page_content_parts:
        full_page_content_text = "No existing wiki pages were identified as candidates for this session."
    else:
        full_page_content_text = "\n\n".join(full_page_content_parts)

    # Also include the candidate facts as context for the LLM
    candidate_facts_json = json.dumps(candidate_facts, indent=2) if candidate_facts else "[]"

    prompt = (
        f"## Session: {title}\n"
        f"## Session ID: {session_id}\n"
        f"## Pass: {pass_num}/2 -- Enrichment Decision\n\n"
        f"=== Candidate Facts (from first pass) ===\n"
        f"{candidate_facts_json}\n\n"
        f"=== Full Page Content ===\n"
        f"{full_page_content_text}\n\n"
        f"=== Session Transcript ===\n"
        f"{transcript}\n\n"
        f"---\n\n"
        f"{ENRICH_PROMPT.replace('<session_id>', session_id)}"
    )
    return prompt


def build_extraction_batch(new_sessions_data: list) -> list:
    """Build a list of extraction tasks. Each task: {session_id, title, prompt}."""
    tasks = []
    for sid, title, messages in new_sessions_data:
        transcript_parts = []
        for role, content in messages:
            label = "USER" if role == "user" else "ASSISTANT"
            transcript_parts.append(f"[{label}]\n{content}")
        transcript = "\n\n".join(transcript_parts)

        prompt = build_candidate_prompt(sid, title, transcript, pass_num=1)
        tasks.append({"session_id": sid, "title": title, "prompt": prompt})

    return tasks


# -- Decision auto-injection into linked pages --------------------------------

def inject_decision_into_linked_pages(decision_title: str, decision_text: str, related_pages: list, existing_pages: dict):
    """Add a '## Key Decisions' section to linked entity/concept pages.

    When a settled decision is written, this injects a bullet point into the
    ## Key Decisions section of each related entity/concept page.
    """
    if not related_pages or not isinstance(related_pages, list):
        return

    for rp_title in related_pages:
        if not rp_title or not rp_title.strip():
            continue
        rp_lower = rp_title.strip().lower()
        rp_path = existing_pages.get(rp_lower)
        if not rp_path:
            continue

        try:
            old_content = rp_path.read_text(encoding="utf-8", errors="replace")
            fm, body = parse_page_content(old_content)

            # Check if this decision is already listed
            entry_line = f"- [[{decision_title}]]"
            if entry_line in body:
                continue

            # Check if ## Key Decisions section exists
            kd_marker = "## Key Decisions"
            if kd_marker in body:
                # Insert after the header
                kd_pos = body.index(kd_marker)
                # Find the end of the section (next heading or EOF)
                rest = body[kd_pos + len(kd_marker):]
                next_heading = re.search(r"\n## ", rest)
                if next_heading:
                    insert_pos = kd_pos + len(kd_marker) + next_heading.start()
                else:
                    insert_pos = len(body)

                # Insert the new entry before the next heading
                new_body = body[:insert_pos] + f"\n- {entry_line} — {decision_text}" + body[insert_pos:]
            else:
                # Append new section
                new_body = body + f"\n\n{kd_marker}\n- {entry_line} — {decision_text}\n"

            # Stamp frontmatter, then write
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            fm["updated"] = today
            new_content = rebuild_page_content(fm, new_body)
            rp_path.write_text(new_content)
            log(f"Decision injected into {rp_path.relative_to(WIKI_DIR)}: [[{decision_title}]]")
        except Exception as e:
            log(f"Failed to inject decision into {rp_path.name}: {e}", "WARN")


# -- Fact writing (called in --ingest mode and auto mode) --------------------

def write_fact(fact: dict, existing_pages: dict, source_session: str = None) -> tuple:
    """Write an OKF page for one fact. Returns (index_entry, action) or None.

    index_entry: (title, ftype) for index.md update
    action: 'created' or 'enriched' -- for tracking in log/heartbeat

    Supports body_action field from LLM:
      - "append" (default): add new summary to existing body
      - "replace": find `replaces` string in existing body and swap with summary

    Uses touch_page() for ALL writes so frontmatter (updated:, sources:) is
    always stamped. Backlinks also go through add_backlinks() -> touch_page().
    """
    ftype = fact.get("type", "concept")
    if ftype not in FACT_DIRS:
        ftype = "concept"

    title = fact.get("title", "").strip()
    if not title:
        return None, None

    confidence = fact.get("confidence", "medium")

    # Decision routing: tentative decisions go to staging/ regardless of confidence
    if ftype == "decision" and fact.get("status") == "tentative":
        subdir = "staging"
    elif confidence == "low":
        subdir = "staging"
    else:
        subdir = FACT_DIRS.get(ftype, "concepts")

    filename = safe_filename(title) + ".md"
    filepath = WIKI_DIR / subdir / filename

    # Check if a page with the same title already exists
    title_lower = title.lower()
    existing_path = existing_pages.get(title_lower)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    summary = fact.get("summary", "")

    if existing_path:
        # -- Enrich existing page ---------------------------------------------
        old_content = existing_path.read_text(encoding="utf-8", errors="replace")
        fm, old_body = parse_page_content(old_content)

        body_action = fact.get("body_action", "append")

        # -- Dedup checks (shared by both append paths) --

        # Primary: session-level idempotency — same session already contributed?
        already_from_this_session = source_session and source_session in fm.get("sources", [])

        # Secondary: content-level dedup — same summary text already in body?
        norm_summary = summary.strip().lower()
        norm_body = old_body.strip().lower()
        dedup_key = norm_summary[:120].strip()
        content_dup = bool(dedup_key) and (
            (len(dedup_key) > 40 and dedup_key in norm_body)
            or (len(norm_summary) <= 40 and norm_summary in norm_body)  # exact match for short summaries
        )

        should_skip_append = already_from_this_session or content_dup

        if body_action == "replace":
            # Targeted replacement
            replaces = fact.get("replaces", "")
            if replaces and replaces in old_body:
                new_body = old_body.replace(replaces, summary, 1)
                log(f"  body_action=replace: swapping '{replaces[:60]}...' -> new content")
            elif should_skip_append:
                dedup_reason = "same session" if already_from_this_session else "content match"
                log(f"  Dedup skip ({dedup_reason}) — replace fallback")
                new_body = old_body
            else:
                log(f"  body_action=replace but '{replaces[:60]}...' not found in body, falling back to append", "WARN")
                new_body = old_body + f"\n\n---\n\n{summary}"
        else:
            if should_skip_append:
                dedup_reason = "same session" if already_from_this_session else "content match"
                log(f"  Dedup skip ({dedup_reason})")
                new_body = old_body
            else:
                # Append new summary to existing body
                new_body = old_body + f"\n\n---\n\n{summary}"

        # Build frontmatter with updates, then write once
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        fm["updated"] = today
        fm["confidence"] = confidence

        # Merge tags
        new_tags = fact.get("tags", [])
        if isinstance(new_tags, list):
            existing_tags = fm.get("tags", [])
            if not isinstance(existing_tags, list):
                existing_tags = [existing_tags] if existing_tags else []
            fm["tags"] = list(dict.fromkeys(existing_tags + new_tags))

        # Merge sources
        if source_session:
            existing_sources = fm.get("sources", [])
            if not isinstance(existing_sources, list):
                existing_sources = [existing_sources] if existing_sources else []
            if source_session not in existing_sources:
                fm["sources"] = existing_sources + [source_session]

        # Merge frontmatter: intent, decision-specific fields
        intent = fact.get("intent")
        if intent:
            fm["intent"] = intent

        if ftype == "decision":
            for key in ("decision", "why", "rejected_alternatives", "decision_weight", "status"):
                val = fact.get(key)
                if val:
                    fm[key] = val

        if ftype == "question":
            qs = fact.get("question_status")
            if qs:
                fm["question_status"] = qs

        new_content = rebuild_page_content(fm, new_body)
        existing_path.write_text(new_content)

        log(f"Enriched: {subdir}/{filename} (confidence={confidence}, body_action={body_action})")

        # Auto-inject ## Key Decisions into linked entity/concept pages
        if ftype == "decision" and fact.get("status") == "settled":
            decision_text = fact.get("decision", summary)[:120]
            inject_decision_into_linked_pages(title, decision_text, fact.get("related_pages", []), existing_pages)

        # Add backlinks to related pages
        related = fact.get("related_pages", [])
        if isinstance(related, list):
            add_backlinks(title, related, existing_pages, source_session=source_session)

        return (title, ftype, "enriched", subdir)

    else:
        # -- Create new page -------------------------------------------------
        frontmatter = {
            "title": title,
            "created": today,
            "updated": today,
            "type": ftype,
            "tags": fact.get("tags", []),
            "confidence": confidence,
            "sources": fact.get("sources", []),
            "intent": fact.get("intent"),
        }
        if not isinstance(frontmatter["sources"], list):
            frontmatter["sources"] = [str(frontmatter["sources"])]

        # Add decision-specific frontmatter
        if ftype == "decision":
            for key in ("decision", "why", "rejected_alternatives", "decision_weight", "status"):
                val = fact.get(key)
                if val:
                    frontmatter[key] = val

        # Add question-specific frontmatter
        if ftype == "question":
            qs = fact.get("question_status")
            if qs:
                frontmatter["question_status"] = qs

        # Strip None values from frontmatter
        frontmatter = {k: v for k, v in frontmatter.items() if v is not None}

        content = rebuild_page_content(frontmatter, f"# {title}\n\n{summary}")

        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_text(content)

        log(f"Created: {subdir}/{filename} (confidence={confidence})")

        # Auto-inject ## Key Decisions into linked entity/concept pages
        if ftype == "decision" and fact.get("status") == "settled":
            decision_text = fact.get("decision", summary)[:120]
            inject_decision_into_linked_pages(title, decision_text, fact.get("related_pages", []), existing_pages)

        # Add backlinks to related pages
        related = fact.get("related_pages", [])
        if isinstance(related, list):
            add_backlinks(title, related, existing_pages, source_session=source_session)

        return (title, ftype, "created", subdir)


# -- Ingest mode: read extracted facts from stdin ----------------------------

def ingest_facts(checkpoint_log: list, facts_input: Optional[str] = None):
    """Read JSON lines from stdin (or facts_input), write wiki pages, commit.

    checkpoint_log: list of (session_id, title) that were extracted this run.
    facts_input: optional JSON string. If provided, use instead of stdin.
    """
    existing = get_existing_pages()
    all_new_titles = []
    fact_count = 0
    created_count = 0
    enriched_count = 0
    staging_count = 0

    # Read all of stdin (or facts_input if provided)
    raw_input = facts_input if facts_input is not None else sys.stdin.read().strip()
    if not raw_input:
        log("No input on stdin, nothing to ingest")
        append_log(f"## [{datetime.now(timezone.utc).strftime('%Y-%m-%d')}] session-to-wiki | OK -- 0 facts (empty LLM response)")
        git_commit("extraction: heartbeat only")
        return

    try:
        facts = json.loads(raw_input)
        if not isinstance(facts, list):
            facts = [facts]
    except json.JSONDecodeError:
        log("Stdin is not valid JSON, treating as raw text", "WARN")
        log(f"Got: {raw_input[:200]}...", "WARN")
        append_log(f"## [{datetime.now(timezone.utc).strftime('%Y-%m-%d')}] session-to-wiki | WARN -- bad JSON from LLM")

        # Try to salvage: wrap in array
        try:
            facts = json.loads(f"[{raw_input}]")
        except (json.JSONDecodeError, TypeError):
            facts = []

    for fact in facts:
        if not isinstance(fact, dict):
            continue
        result = write_fact(fact, existing, source_session=checkpoint_log[0][0] if checkpoint_log else None)
        if result:
            entry, _ftype, action, _subdir = result
            # For index: only track newly created pages, not enriched ones
            if action == "created":
                all_new_titles.append((entry, _ftype))
                created_count += 1
            else:
                enriched_count += 1
            fact_count += 1
            if fact.get("confidence") == "low":
                staging_count += 1

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    session_log = "; ".join(f"{sid[:8]}: {title}" for sid, title in checkpoint_log[:5])
    if len(checkpoint_log) > 5:
        session_log += f" (+{len(checkpoint_log)-5} more)"

    heartbeat = (
        f"## [{today}] session-to-wiki | OK -- "
        f"{len(checkpoint_log)} sessions, "
        f"{fact_count} facts "
        f"({created_count} created, {enriched_count} enriched, {staging_count} staging)"
    )

    # Checkpoint each session
    for sid, _title in checkpoint_log:
        save_checkpoint(sid)

    if all_new_titles:
        update_index_with_entries(all_new_titles)
    append_log(heartbeat)
    git_commit(f"extraction: {len(checkpoint_log)} sessions, {fact_count} facts ({created_count} created, {enriched_count} enriched, {staging_count} staging)")
    log(f"Ingested: {fact_count} facts from {len(checkpoint_log)} sessions ({created_count} created, {enriched_count} enriched)")
    print(json.dumps({"status": "ok", "sessions": len(checkpoint_log), "facts": fact_count, "created": created_count, "enriched": enriched_count, "staging": staging_count}))


# -- Extract mode: query DB, print prompts to stdout -------------------------

def extract_new(reprocess: bool = False):
    """Query Hermes DB for new sessions, print extraction prompts to stdout."""
    processed = set() if reprocess else load_checkpoint()
    log(f"Checkpoint: {len(processed)} sessions already processed")

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Get unprocessed sessions using SQL-level filter (not Python)
    # ORDER BY s.id ASC ensures oldest-first ordering -- seeded entities are
    # established early during backfill, giving the LLM enrichment targets
    # from the very first session.
    if reprocess:
        cursor.execute("""
            SELECT s.id, s.title
            FROM sessions s
            WHERE EXISTS (
                SELECT 1 FROM messages m
                WHERE m.session_id = s.id
                  AND m.role IN ('user', 'assistant')
                  AND m.active = 1
                  AND m.compacted = 0
            )
            ORDER BY s.id ASC
        """)
    else:
        # Build placeholders
        processed_list = list(processed)
        if processed_list:
            placeholders = ",".join("?" for _ in processed_list)
            cursor.execute(f"""
                SELECT s.id, s.title
                FROM sessions s
                WHERE s.id NOT IN ({placeholders})
                  AND EXISTS (
                      SELECT 1 FROM messages m
                      WHERE m.session_id = s.id
                        AND m.role IN ('user', 'assistant')
                        AND m.active = 1
                        AND m.compacted = 0
                  )
                ORDER BY s.id ASC
            """, processed_list)
        else:
            cursor.execute("""
                SELECT s.id, s.title
                FROM sessions s
                WHERE EXISTS (
                    SELECT 1 FROM messages m
                    WHERE m.session_id = s.id
                      AND m.role IN ('user', 'assistant')
                      AND m.active = 1
                      AND m.compacted = 0
                )
                ORDER BY s.id ASC
            """)

    sessions = cursor.fetchall()
    log(f"Found {len(sessions)} unprocessed sessions")

    if not sessions:
        log("No new sessions to process")
        conn.close()
        return []

    # Build extraction data and print to stdout
    all_tasks = []
    all_checkpoint = []

    for session in sessions:
        sid = session["id"]
        title = session["title"] or sid[:8]

        cursor.execute("""
            SELECT role, content FROM messages
            WHERE session_id = ?
              AND role IN ('user', 'assistant')
              AND active = 1
              AND compacted = 0
            ORDER BY id ASC
        """, (sid,))

        messages = cursor.fetchall()
        if len(messages) < MIN_MESSAGES:
            log(f"Skipping {sid[:8]}: only {len(messages)} messages", "WARN")
            save_checkpoint(sid)  # Still checkpoint to avoid re-scanning noise
            continue

        msgs = [(m["role"], m["content"]) for m in messages]
        all_checkpoint.append((sid, title))

        # Print JSON task to stdout for cron agent
        task = {
            "session_id": sid,
            "title": title,
            "messages": [
                {"role": r, "content_len": len(c), "content": c[-500:] if len(c) > 2000 else c}
                for r, c in msgs
            ],
            "message_count": len(msgs),
            "total_chars": sum(len(c) for _, c in msgs)
        }
        print(json.dumps(task))

    conn.close()
    log(f"Printed {len(all_checkpoint)} session tasks to stdout")
    return all_checkpoint


# -- Auto mode: extract + LLM + ingest, all in one process -------------------

def run_extraction_pass(session_id: str, title: str, transcript: str,
                        pass_num: int, existing_pages: dict) -> list[dict]:
    """Run the 2-call extraction for one pass.

    Call 1: Send transcript + wiki index. LLM returns facts with candidate_page.
    Call 2: Fetch full content of candidate pages. LLM returns final enrichment facts.

    Returns the final list of fact dicts.
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    # -- Call 1: Candidate naming --
    log(f"  [{pass_num}/2, call 1/2] Naming candidates for {session_id[:8]}")
    candidate_prompt = build_candidate_prompt(session_id, title, transcript, pass_num=pass_num)
    candidate_facts = call_llm_for_extraction(candidate_prompt, session_id)

    if not candidate_facts:
        log(f"  [{pass_num}/2, call 1/2] No candidates from session {session_id[:8]}", "WARN")
        return []

    # Count how many have candidate_page set
    candidate_count = sum(1 for f in candidate_facts if f.get("candidate_page"))
    log(f"  [{pass_num}/2, call 1/2] Got {len(candidate_facts)} facts, "
        f"{candidate_count} with candidate pages")

    # -- Call 2: Enrichment with full page content --
    log(f"  [{pass_num}/2, call 2/2] Enrichment decision for {session_id[:8]}")
    enrich_prompt = build_enrich_prompt(
        session_id, title, transcript, candidate_facts, existing_pages, pass_num=pass_num
    )
    enrich_facts = call_llm_for_extraction(enrich_prompt, session_id)

    if not enrich_facts:
        log(f"  [{pass_num}/2, call 2/2] No enrichment facts from session {session_id[:8]}", "WARN")
        # Fall back to candidate facts (better than nothing)
        log(f"  [{pass_num}/2, call 2/2] Falling back to candidate facts", "WARN")
        return candidate_facts

    log(f"  [{pass_num}/2, call 2/2] Got {len(enrich_facts)} enrichment facts")
    return enrich_facts


def auto_extract_and_ingest(max_sessions: Optional[int] = None, reprocess: bool = False):
    """Full pipeline: extract sessions, call LLM, write wiki pages.

    Per-session 2-pass, 2-call-per-pass design:
      Pass 1, Call 1:   Candidate naming against wiki index
      Pass 1, Call 2:   Enrichment decision with full candidate page content
                        -> write pass 1 output -> git commit
      Pass 2, Call 1:   Candidate naming against updated wiki (pass 1 committed)
      Pass 2, Call 2:   Enrichment decision with full candidate page content
                        -> write pass 2 output -> git commit -> checkpoint

    This means 4 LLM calls per session (2 passes x 2 calls each).
    """
    # Recover from any prior crash before starting
    recover_dirty_tree()

    processed = set() if reprocess else load_checkpoint()
    log(f"Checkpoint: {len(processed)} sessions already processed")

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Get unprocessed sessions -- ORDER BY s.id ASC ensures oldest-first
    # ordering during backfill, establishing core entities early.
    if reprocess:
        cursor.execute("""
            SELECT s.id, s.title
            FROM sessions s
            WHERE EXISTS (
                SELECT 1 FROM messages m
                WHERE m.session_id = s.id
                  AND m.role IN ('user', 'assistant')
                  AND m.active = 1
                  AND m.compacted = 0
            )
            ORDER BY s.id ASC
        """)
    else:
        processed_list = list(processed)
        if processed_list:
            placeholders = ",".join("?" for _ in processed_list)
            cursor.execute(f"""
                SELECT s.id, s.title
                FROM sessions s
                WHERE s.id NOT IN ({placeholders})
                  AND EXISTS (
                      SELECT 1 FROM messages m
                      WHERE m.session_id = s.id
                        AND m.role IN ('user', 'assistant')
                        AND m.active = 1
                        AND m.compacted = 0
                  )
                ORDER BY s.id ASC
            """, processed_list)
        else:
            cursor.execute("""
                SELECT s.id, s.title
                FROM sessions s
                WHERE EXISTS (
                    SELECT 1 FROM messages m
                    WHERE m.session_id = s.id
                      AND m.role IN ('user', 'assistant')
                      AND m.active = 1
                      AND m.compacted = 0
                )
                ORDER BY s.id ASC
            """)

    sessions = cursor.fetchall()
    conn.close()

    log(f"Found {len(sessions)} unprocessed sessions for auto mode")

    if max_sessions and len(sessions) > max_sessions:
        sessions = sessions[:max_sessions]
        log(f"Limiting to {max_sessions} sessions this run", "WARN")

    total_sessions = 0
    total_facts = 0
    total_created = 0
    total_enriched = 0
    total_staging = 0
    total_calls = 0
    checkpointed = []

    for session in sessions:
        sid = session["id"]
        title = session["title"] or sid[:8]

        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            "SELECT role, content FROM messages WHERE session_id = ? AND role IN ('user', 'assistant') AND active = 1 AND compacted = 0 ORDER BY id ASC",
            (sid,))
        msgs = [(m["role"], m["content"]) for m in cursor.fetchall()]
        conn.close()

        if len(msgs) < MIN_MESSAGES:
            save_checkpoint(sid)
            continue

        # Build transcript
        transcript_parts = []
        for role, content in msgs:
            label = "USER" if role == "user" else "ASSISTANT"
            if len(content) > 10000:
                content = content[:10000] + "\n[...truncated...]"
            transcript_parts.append(f"[{label}]\n{content}")
        transcript = "\n\n".join(transcript_parts)

        # -- Pass 1: Extract facts against current wiki state --
        log(f"[Pass 1/2] Processing session {sid[:8]}: {title}")
        existing = get_existing_pages()
        pass1_facts = run_extraction_pass(sid, title, transcript, pass_num=1, existing_pages=existing)
        total_calls += 2  # 2 calls per pass

        # Write pass 1 facts
        pass1_created = 0
        pass1_enriched = 0
        pass1_staging = 0
        pass1_index_entries = []
        existing = get_existing_pages()  # Re-read (catches any prior session's writes)

        for fact in pass1_facts:
            if not isinstance(fact, dict):
                continue
            result = write_fact(fact, existing, source_session=sid)
            if result:
                entry, _ftype, action, _subdir = result
                if action == "created":
                    pass1_index_entries.append((entry, _ftype))
                    pass1_created += 1
                else:
                    pass1_enriched += 1
                if fact.get("confidence") == "low":
                    pass1_staging += 1
                existing[entry.lower()] = WIKI_DIR / _subdir / (safe_filename(entry) + ".md")

        if pass1_index_entries:
            update_index_with_entries(pass1_index_entries)

        git_commit(
            f"extraction: pass 1 -- {sid[:8]}: {title} "
            f"({pass1_created} created, {pass1_enriched} enriched, {pass1_staging} staging)"
        )
        log(f"  Pass 1 complete: {pass1_created + pass1_enriched} facts ({pass1_created} created, {pass1_enriched} enriched, {pass1_staging} staging)")

        # -- Pass 2: Enrich against updated wiki state --
        log(f"[Pass 2/2] Processing session {sid[:8]}: {title} (enrichment pass)")
        existing = get_existing_pages()  # Fresh read -- pass 1's writes are committed
        pass2_facts = run_extraction_pass(sid, title, transcript, pass_num=2, existing_pages=existing)
        total_calls += 2

        # Write pass 2 facts
        pass2_created = 0
        pass2_enriched = 0
        pass2_staging = 0
        pass2_index_entries = []
        existing = get_existing_pages()

        for fact in pass2_facts:
            if not isinstance(fact, dict):
                continue
            result = write_fact(fact, existing, source_session=sid)
            if result:
                entry, _ftype, action, _subdir = result
                if action == "created":
                    pass2_index_entries.append((entry, _ftype))
                    pass2_created += 1
                else:
                    pass2_enriched += 1
                if fact.get("confidence") == "low":
                    pass2_staging += 1
                existing[entry.lower()] = WIKI_DIR / _subdir / (safe_filename(entry) + ".md")

        if pass2_index_entries:
            update_index_with_entries(pass2_index_entries)

        # Per-session summary
        created = pass1_created + pass2_created
        enriched = pass1_enriched + pass2_enriched
        staging = pass1_staging + pass2_staging
        fact_count = created + enriched

        session_log_line = (
            f"## [{datetime.now(timezone.utc).strftime('%Y-%m-%d')}] extraction | "
            f"{sid[:8]}: {title} -- "
            f"{fact_count} facts ({created} created, {enriched} enriched, {staging} staging)"
        )
        append_log(session_log_line)

        git_commit(
            f"extraction: pass 2 -- {sid[:8]}: {title} "
            f"({created} created, {enriched} enriched, {staging} staging)"
        )

        # Checkpoint session (both passes complete)
        save_checkpoint(sid)
        checkpointed.append((sid, title))
        total_sessions += 1
        total_facts += fact_count
        total_created += created
        total_enriched += enriched
        total_staging += staging

        log(f"Session {sid[:8]} complete: {fact_count} facts ({created} created, {enriched} enriched, {staging} staging) -- {total_calls} LLM calls total")

    # Final heartbeat
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    heartbeat = (
        f"## [{today}] session-to-wiki | OK -- "
        f"{total_sessions} sessions, "
        f"{total_facts} facts "
        f"({total_created} created, {total_enriched} enriched, {total_staging} staging)"
    )
    append_log(heartbeat)

    if total_sessions > 0:
        git_commit(
            f"extraction: {total_sessions} sessions, {total_facts} facts "
            f"({total_created} created, {total_enriched} enriched, {total_staging} staging)"
        )

    log(f"Auto mode complete: {total_sessions} sessions, {total_facts} facts, {total_calls} LLM calls")
    print(json.dumps({
        "status": "ok",
        "sessions": total_sessions,
        "facts": total_facts,
        "created": total_created,
        "enriched": total_enriched,
        "staging": total_staging,
        "llm_calls": total_calls
    }))


# -- Main --------------------------------------------------------------------

def main():
    # Version check always
    version_check()

    # Determine mode
    reprocess = "--reprocess" in sys.argv
    ingest_mode = "--ingest" in sys.argv
    auto_mode = "--auto" in sys.argv
    max_sessions = None

    # --reprocess warning
    if reprocess and "--help" not in sys.argv:
        log("WARNING: --reprocess processes ALL sessions, including previously "
            "completed ones. Only use for one-time backfill, not in cron.", "WARN")

    if "--max" in sys.argv:
        try:
            max_sessions = int(sys.argv[sys.argv.index("--max") + 1])
        except (ValueError, IndexError):
            pass

    if "--help" in sys.argv or "-h" in sys.argv:
        print(__doc__)
        print("""
Options:
  --auto          Full pipeline: extract, call LLM, write wiki pages. One-shot.
  --ingest        Read extracted JSON from stdin, write wiki pages, checkpoint.
  --max N         Limit to N sessions per run (default: no limit).
  --reprocess     Process ALL sessions, including previously completed ones.
                  WARNING: Only use for one-time backfill, not in cron.
  --help, -h      Show this help message.
        """)
        return

    if auto_mode:
        if not acquire_lock():
            sys.exit(1)
        try:
            auto_extract_and_ingest(max_sessions=max_sessions, reprocess=reprocess)
        except Exception as e:
            log(f"Error during auto extraction: {e}", "ERROR")
            raise
        finally:
            release_lock()
        return

    if ingest_mode:
        # Read checkpoint info from JSON on stdin's first line (metadata)
        # Then read facts JSON
        if not acquire_lock():
            sys.exit(1)
        try:
            # Checkpoint info passed via env var or first line
            checkpoint_json = os.environ.get("WIKI_CHECKPOINT", "[]")
            checkpoint_log = json.loads(checkpoint_json) if isinstance(json.loads(checkpoint_json), list) else []
            ingest_facts(checkpoint_log)
        finally:
            release_lock()
        return

    # Extract mode: acquire lock, query DB, print tasks
    if not acquire_lock():
        sys.exit(1)

    try:
        checkpoint_log = extract_new(reprocess=reprocess)

        if not checkpoint_log:
            release_lock()
            return

        # Print checkpoint metadata as last JSON line (for cron agent to capture)
        meta = {
            "_checkpoint": [{"session_id": sid, "title": title} for sid, title in checkpoint_log],
            "_note": "Pipe these tasks to an LLM, then pipe the JSON result back with --ingest"
        }
        print(json.dumps(meta))

    except Exception as e:
        log(f"Error during extraction: {e}", "ERROR")
        # Don't checkpoint anything on error
        raise
    finally:
        release_lock()


if __name__ == "__main__":
    main()