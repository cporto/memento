#!/usr/bin/env python3
"""Functional tests for session-to-wiki.py v6 — pure-logic paths, no LLM/DB."""
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="memento-test-"))
WIKI = TMP / "wiki"
os.environ["MEMENTO_WIKI_DIR"] = str(WIKI)
os.environ["WIKI_LOCK_DIR"] = str(TMP / "locks")
os.environ["MEMENTO_HINTS_FILE"] = str(TMP / "hints.md")
os.environ["MEMENTO_TRANSCRIPT_BUDGET"] = "500"

spec = importlib.util.spec_from_file_location("s2w", Path(__file__).parent / "session-to-wiki.py")
s2w = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s2w)

# Point module paths at scratch wiki (module computed them at import from env)
assert s2w.WIKI_DIR == WIKI, f"WIKI_DIR env override failed: {s2w.WIKI_DIR}"

for d in ["entities", "concepts", "decisions", "questions", "comparisons", "staging"]:
    (WIKI / d).mkdir(parents=True, exist_ok=True)
subprocess.run(["git", "-C", str(WIKI), "init", "-q"], check=True)
subprocess.run(["git", "-C", str(WIKI), "config", "user.email", "t@t"], check=True)
subprocess.run(["git", "-C", str(WIKI), "config", "user.name", "t"], check=True)

PASS = []
FAIL = []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"{'PASS' if cond else 'FAIL'}: {name}" + (f" -- {detail}" if detail and not cond else ""))


# ---- 1. slug + collision ----------------------------------------------------
check("slug basic", s2w.safe_filename("oMLX: Config!") == "omlx-config")

fact_a = {"type": "entity", "title": "oMLX Config", "summary": "First page about config.",
          "tags": ["tooling"], "confidence": "high", "sources": ["s1"], "related_pages": []}
fact_b = {"type": "entity", "title": "oMLX: config!", "summary": "A different title, same slug.",
          "tags": ["tooling"], "confidence": "high", "sources": ["s1"], "related_pages": []}

contrib = set()
existing = s2w.get_existing_pages()
t, ft, act, sd = s2w.write_fact(fact_a, existing, "s1", 1, contrib)
check("create A", act == "created")
existing = s2w.get_existing_pages()
t, ft, act, sd = s2w.write_fact(fact_b, existing, "s1", 1, contrib)
files = sorted(p.name for p in (WIKI / "entities").glob("omlx-config*"))
check("slug collision -> two files, no clobber", len(files) == 2, str(files))
orig = (WIKI / "entities" / "omlx-config.md").read_text()
check("original page content intact", "First page about config" in orig)

# ---- 2. pass-aware dedup (the pass-2 neutering bug) -------------------------
fact_p1 = {"type": "entity", "title": "Atlas", "summary": "Home server, runs backups nightly.",
           "tags": ["hardware"], "confidence": "high", "sources": ["sess9"], "related_pages": []}
contrib = set()
existing = s2w.get_existing_pages()
s2w.write_fact(fact_p1, existing, "sess9", 1, contrib)  # pass 1 creates
existing = s2w.get_existing_pages()
fact_p2 = {"type": "entity", "title": "Atlas", "summary": "Also hosts the SearXNG instance for local search.",
           "tags": ["hardware"], "confidence": "high", "sources": ["sess9"], "related_pages": [],
           "body_action": "append"}
t, ft, act, sd = s2w.write_fact(fact_p2, existing, "sess9", 2, contrib)  # pass 2, SAME session
atlas = (WIKI / "entities" / "atlas.md").read_text()
check("pass 2 same-session append NOT blocked", "SearXNG" in atlas and act == "enriched")

# Same pass, same session, duplicate content -> should skip
fact_dup = dict(fact_p2)
t, ft, act, sd = s2w.write_fact(fact_dup, s2w.get_existing_pages(), "sess9", 2, contrib)
check("same-pass repeat skipped", (WIKI / "entities" / "atlas.md").read_text().count("SearXNG") == 1)

# ---- 3. confidence gating on enrichment ------------------------------------
fact_low = {"type": "entity", "title": "Atlas", "summary": "Possibly also runs a Minecraft server?",
            "tags": [], "confidence": "low", "sources": ["sessA"], "related_pages": []}
t, ft, act, sd = s2w.write_fact(fact_low, s2w.get_existing_pages(), "sessA", 1, set())
atlas = (WIKI / "entities" / "atlas.md").read_text()
pend = WIKI / "staging" / "atlas--pending.md"
check("low-conf fact diverted from live page", "Minecraft" not in atlas and act == "staged-pending")
check("pending file created", pend.exists() and "Minecraft" in pend.read_text())

# Confidence can only go down
fm, _ = s2w.parse_page_content(atlas)
check("live page confidence unchanged by low fact", fm.get("confidence") == "high")
fact_med = {"type": "entity", "title": "Atlas", "summary": "RAM upgraded recently, size unconfirmed.",
            "tags": [], "confidence": "medium", "sources": ["sessB"], "related_pages": [], "body_action": "append"}
s2w.write_fact(fact_med, s2w.get_existing_pages(), "sessB", 1, set())
fm, _ = s2w.parse_page_content((WIKI / "entities" / "atlas.md").read_text())
check("confidence lowered by medium fact (min policy)", fm.get("confidence") == "medium")
fact_high = {"type": "entity", "title": "Atlas", "summary": "Confirmed: hostname resolves on LAN.",
             "tags": [], "confidence": "high", "sources": ["sessC"], "related_pages": [], "body_action": "append"}
s2w.write_fact(fact_high, s2w.get_existing_pages(), "sessC", 1, set())
fm, _ = s2w.parse_page_content((WIKI / "entities" / "atlas.md").read_text())
check("confidence NOT laundered back up", fm.get("confidence") == "medium")

# ---- 4. replace path + audit log -------------------------------------------
fact_rep = {"type": "entity", "title": "Atlas", "summary": "Home server, runs backups hourly.",
            "tags": [], "confidence": "high", "sources": ["sessD"], "related_pages": [],
            "body_action": "replace", "replaces": "Home server, runs backups nightly."}
s2w.write_fact(fact_rep, s2w.get_existing_pages(), "sessD", 1, set())
atlas = (WIKI / "entities" / "atlas.md").read_text()
check("replace applied", "hourly" in atlas and "nightly" not in atlas)
check("replace audited in log.md", "REPLACE on [[Atlas]]" in (WIKI / "log.md").read_text())

# ---- 5. backlinks sentinel block: idempotent + legacy migration -------------
nas = WIKI / "entities" / "nas-box.md"
nas.write_text("---\ntitle: nas-box\ntype: entity\n---\n\n# nas-box\n\nStorage box.\n\nBacklinks: [[Old Page]]\n")
existing = s2w.get_existing_pages()
s2w.add_backlinks("Atlas", ["nas-box"], existing, "sessE")
s2w.add_backlinks("Atlas", ["nas-box"], existing, "sessE")  # repeat
content = nas.read_text()
check("backlinks: single block", content.count(s2w.BACKLINKS_START) == 1)
check("backlinks: legacy line migrated", "Old Page" in content and "Backlinks: [[Old Page]]\n" not in content.replace(s2w.BACKLINKS_START, ""))
check("backlinks: no duplicate entry", content.count("[[Atlas]]") == 1)

# ---- 6. key decisions sentinel: idempotent ----------------------------------
existing = s2w.get_existing_pages()
s2w.inject_decision_into_linked_pages("Use rsync", "chose rsync over restic", ["nas-box"], existing)
s2w.inject_decision_into_linked_pages("Use rsync", "chose rsync over restic", ["nas-box"], existing)
content = nas.read_text()
check("key decisions: single block, single entry",
      content.count(s2w.KEYDEC_START) == 1 and content.count("[[Use rsync]]") == 1)

# ---- 7. index update: fixed pre-filter --------------------------------------
s2w.update_index_with_entries([("Atlas", "entity"), ("Zeta Concept", "concept")])
idx1 = (WIKI / "index.md").read_text()
s2w.update_index_with_entries([("Atlas", "entity")])  # already present
idx2 = (WIKI / "index.md").read_text()
check("index: entry under correct section", "## Entities" in idx1 and "- [[Atlas]]" in idx1)
check("index: no duplicate on re-add", idx2.count("- [[Atlas]]") == 1)

# ---- 8. chunking + truncation ----------------------------------------------
msgs = [("user", "a" * 300), ("assistant", "b" * 300), ("user", "c" * 300)]
chunks = s2w.build_transcript_chunks(msgs)  # budget=500 via env
check("chunking splits over budget", len(chunks) >= 2, f"{len(chunks)} chunks")
check("chunking loses no messages", "".join(chunks).count("[USER]") + "".join(chunks).count("[ASSISTANT]") == 3)
tight = s2w.build_transcript_chunks(msgs, budget=310)
check("chunking honors explicit budget", len(tight) == 3, f"{len(tight)} chunks at budget=310")
big = "x" * 20000
tr = s2w.truncate_message(big)
check("per-msg truncation head+tail", tr.startswith("x" * 100) and tr.endswith("x" * 100) and "elided" in tr)

# ---- 9. secret redaction ----------------------------------------------------
red = s2w.redact_secrets("key is sk-abc123def456ghi789jkl and AKIAIOSFODNN7EXAMPLE ok")
check("secrets redacted", "sk-abc" not in red and "AKIA" not in red and "[REDACTED-SECRET]" in red)

# ---- 10. retry file lifecycle ----------------------------------------------
s2w.record_retry("sess-x", 1, "network down")
s2w.record_retry("sess-x", 2, "network down again")
check("retry attempts tracked", s2w.load_retries().get("sess-x") == 2)
s2w.clear_retry("sess-x")
check("retry cleared", "sess-x" not in s2w.load_retries())

# ---- 11. ingest per-fact attribution ---------------------------------------
subprocess.run(["git", "-C", str(WIKI), "add", "-A"], check=True)
subprocess.run(["git", "-C", str(WIKI), "commit", "-qm", "pre-ingest"], check=True)
facts_json = s2w.json.dumps([
    {"type": "concept", "title": "Concept One", "summary": "From session AAA.",
     "tags": [], "confidence": "high", "sources": ["sessAAA"], "related_pages": []},
    {"type": "concept", "title": "Concept Two", "summary": "From session BBB.",
     "tags": [], "confidence": "high", "sources": ["sessBBB"], "related_pages": []},
])
ckpt = [{"session_id": "sessAAA", "title": "A"}, {"session_id": "sessBBB", "title": "B"}]
s2w.ingest_facts(ckpt, facts_input=facts_json)
c1_fm, _ = s2w.parse_page_content((WIKI / "concepts" / "concept-one.md").read_text())
c2_fm, _ = s2w.parse_page_content((WIKI / "concepts" / "concept-two.md").read_text())
check("ingest: dict checkpoint parsed (no crash)", True)
check("ingest: fact 1 attributed to its own session", "sessAAA" in c1_fm.get("sources", []) and "sessBBB" not in c1_fm.get("sources", []))
check("ingest: fact 2 attributed to its own session", "sessBBB" in c2_fm.get("sources", []))
checkpointed = s2w.load_checkpoint()
check("ingest: sessions checkpointed", {"sessAAA", "sessBBB"} <= checkpointed)

# ---- 12. promote lifecycle --------------------------------------------------
stg = WIKI / "staging" / "tentative-thing.md"
stg.write_text("---\ntitle: Tentative Thing\ntype: decision\nconfidence: low\nstatus: tentative\n---\n\n# Tentative Thing\n\nWe might do X.\n")
s2w.promote_staging("tentative-thing")
dest = WIKI / "decisions" / "tentative-thing.md"
check("promote: moved to live dir", dest.exists() and not stg.exists())
fm, _ = s2w.parse_page_content(dest.read_text())
check("promote: confidence bumped, status settled",
      fm.get("confidence") == "medium" and fm.get("status") == "settled")
check("promote: indexed", "- [[Tentative Thing]]" in (WIKI / "index.md").read_text())

# ---- 13. lock script --------------------------------------------------------
lock = Path(__file__).parent / "wiki-lock.sh"
os.chmod(lock, 0o755)
env = {**os.environ}
r1 = subprocess.run([str(lock), "acquire", "test"], env=env)
r2 = subprocess.run([str(lock), "acquire", "test"], env=env)
r3 = subprocess.run([str(lock), "release", "test"], env=env)
r4 = subprocess.run([str(lock), "acquire", "test"], env=env)
subprocess.run([str(lock), "release", "test"], env=env)
check("lock: acquire/held/release cycle", r1.returncode == 0 and r2.returncode == 1 and r3.returncode == 0 and r4.returncode == 0)
check("lock: honors WIKI_LOCK_DIR", (TMP / "locks").exists())
rbad = subprocess.run([str(lock), "acquire", "../evil"], env=env, capture_output=True)
check("lock: rejects path traversal", rbad.returncode == 64)

print(f"\n{'='*60}\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILURES:", FAIL)
    sys.exit(1)
