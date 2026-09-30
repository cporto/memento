#!/usr/bin/env python3
"""memento-session-prep.py — universal pre-extraction classifier (the PREP step).

Profiles ANY Memento session deterministically (one GROUP BY pass, no LLM) and
emits a HANDLING POINTER that tells the extractor how to process it efficiently:

  SHORT      -> post-filter < SHORT_THRESHOLD -> single pass (skip pass 2), tight budget.
  CODE_PASTE -> user is the volume driver pasting code -> 2-pass, raise per-message cap
                so head+tail elision doesn't mangle pasted code; tool dumps already excluded.
  TOOL_BLOAT -> stored chars dominated by tool dumps, thin user thread -> single pass
                over post-filter (ask + final conclusion + decisions). Every mode EXTRACTS;
                'light' means fewer LLM calls, never fewer sessions.
  BALANCED   -> otherwise -> normal 2-pass.

Fused verdict (giant-backlog-2, 2026-09-30): ALL sessions are real user data — never
drop/archive/label as junk. This classifier only selects cost/accuracy mode; it never
decides retention.

Usage:
  memento-session-prep.py <sid>          # one session (sid or sid-prefix, LIKE match)
  memento-session-prep.py --list <file>  # read sids (one per line) -> table
  memento-session-prep.py --diverted     # run over ~/wiki/.diverted-giant top-10 (bulk planning)

Env:
  MEMENTO_DB         state.db path (default ~/.hermes/state.db)
  PREP_SHORT_KB      post-filter threshold for SHORT (default 20)
  PREP_BLOAT_RATIO   stored >= ratio * post-filter => TOOL_BLOAT candidate (default 3)
  PREP_CODE_FRACTION user_pf >= fraction * pf => user-dominant (default 0.5)
  PREP_CODE_MSG      user message >= this chars => 'big paste' (default 50000)
  PREP_CODE_FENCE    min ``` fence count in user rows to count as code paste (default 3)

Output line (tab-separated):
  <sid> \t <POINTER> \t <pf_chars> \t <stored_chars> \t <user_pf> \t <asst_pf> \
         \t <tool_chars> \t <biggest_user> \t <code_fences>
"""
import os, sys, sqlite3

DB = os.environ.get("MEMENTO_DB", os.path.expanduser("~/.hermes/state.db"))
SHORT_KB = float(os.environ.get("PREP_SHORT_KB", "20"))
BLOAT_RATIO = float(os.environ.get("PREP_BLOAT_RATIO", "3"))
CODE_FRACTION = float(os.environ.get("PREP_CODE_FRACTION", "0.5"))
CODE_MSG = int(os.environ.get("PREP_CODE_MSG", "50000"))
CODE_FENCE = int(os.environ.get("PREP_CODE_FENCE", "3"))


def open_map():
    return sqlite3.connect(DB)


def profile(c, sid):
    """One deterministic pass: role stats + post-filter + code-paste heuristic."""
    cur = c.execute(
        "SELECT role, COUNT(*), COALESCE(SUM(LENGTH(COALESCE(content,''))),0), "
        "COALESCE(MAX(LENGTH(COALESCE(content,''))),0) "
        "FROM messages WHERE session_id=? GROUP BY role", (sid,))
    by = {}
    for role, cnt, chars, mx in cur.fetchall():
        by[role] = (cnt, chars, mx)
    tool_chars = by.get("tool", (0, 0, 0))[1]
    stored_chars = sum(v[1] for v in by.values())
    cur = c.execute(
        "SELECT COUNT(*), COALESCE(SUM(LENGTH(COALESCE(content,''))),0), "
        "COALESCE(MAX(LENGTH(COALESCE(content,''))),0) "
        "FROM messages WHERE session_id=? AND role IN ('user','assistant') "
        "AND active=1 AND compacted=0", (sid,))
    (pf_cnt, pf_chars, _) = cur.fetchone()
    cur = c.execute(
        "SELECT COALESCE(SUM(LENGTH(COALESCE(content,''))),0) FROM messages "
        "WHERE session_id=? AND role='user' AND active=1 AND compacted=0", (sid,))
    user_pf = cur.fetchone()[0]
    cur = c.execute(
        "SELECT COALESCE(SUM(LENGTH(COALESCE(content,''))),0) FROM messages "
        "WHERE session_id=? AND role='assistant' AND active=1 AND compacted=0", (sid,))
    asst_pf = cur.fetchone()[0]
    cur = c.execute(
        "SELECT COALESCE(MAX(LENGTH(COALESCE(content,''))),0) FROM messages "
        "WHERE session_id=? AND role='user' AND active=1 AND compacted=0", (sid,))
    biggest_user = cur.fetchone()[0]
    cur = c.execute(
        "SELECT COALESCE(SUM(content LIKE '%```%'),0) FROM messages "
        "WHERE session_id=? AND role='user' AND active=1 AND compacted=0", (sid,))
    code_fences = cur.fetchone()[0]
    return {
        "sid": sid, "stored_chars": stored_chars, "pf_chars": pf_chars,
        "user_pf": user_pf, "asst_pf": asst_pf, "tool_chars": tool_chars,
        "biggest_user": biggest_user, "code_fences": code_fences,
    }


def classify(p):
    """Emit the handling POINTER from the profile (every outcome EXTRACTS)."""
    pf = p["pf_chars"]
    if pf < SHORT_KB * 1024:
        return "SHORT"
    if p["stored_chars"] >= BLOAT_RATIO * max(pf, 1) and p["user_pf"] < CODE_FRACTION * max(pf, 1):
        return "TOOL_BLOAT"
    user_dominant = p["user_pf"] >= CODE_FRACTION * max(pf, 1)
    codey = (p["code_fences"] >= CODE_FENCE) or (p["biggest_user"] >= CODE_MSG)
    if user_dominant and codey:
        return "CODE_PASTE"
    return "BALANCED"


def resolve_sid(c, sid):
    cur = c.execute("SELECT id FROM sessions WHERE id=?", (sid,))
    r = cur.fetchone()
    if r:
        return r[0]
    cur = c.execute("SELECT id FROM sessions WHERE id LIKE ? LIMIT 1", (sid + "%",))
    r = cur.fetchone()
    return r[0] if r else None


def main():
    args = sys.argv[1:]
    c = open_map()
    sids = []
    if not args:
        print(__doc__)
        sys.exit(0)
    if args[0] == "--diverted":
        divert = os.path.expanduser("~/wiki/.diverted-giant")
        with open(divert) as f:
            sids = [ln.split("\t")[0] for ln in f if ln.strip()]
        sids = sids[:10]
    elif args[0] == "--list" and len(args) > 1:
        with open(args[1]) as f:
            sids = [ln.strip() for ln in f if ln.strip()]
    else:
        sids = [args[0]]
    print("# sid\tPOINTER\tpf_chars\tstored\tuser_pf\tasst_pf\ttool_chars\tbiggest_user\tcode_fences")
    for sid in sids:
        full = resolve_sid(c, sid)
        if not full:
            print(f"{sid}\tNO_SESSION\t0\t0\t0\t0\t0\t0\t0")
            continue
        p = profile(c, full)
        pointer = classify(p)
        print(f"{full}\t{pointer}\t{p['pf_chars']}\t{p['stored_chars']}\t"
              f"{p['user_pf']}\t{p['asst_pf']}\t{p['tool_chars']}\t"
              f"{p['biggest_user']}\t{p['code_fences']}")


if __name__ == "__main__":
    main()
