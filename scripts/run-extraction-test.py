#!/usr/bin/env python3
"""Extraction Model Comparison Test — H4-14B vs Gemma-4-E4B

Runs both models on all 3 test sessions, saves raw output for comparison.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request

# Add session-to-wiki.py dir to path and import the LLM function directly
# Since the script has hyphens, we import the module manually
sys.path.insert(0, os.path.expanduser("~/.hermes/scripts"))
import importlib.util
spec = importlib.util.spec_from_file_location("session_to_wiki", os.path.expanduser("~/.hermes/scripts/session-to-wiki.py"))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
call_llm_for_extraction = mod.call_llm_for_extraction

# Test sessions
SESSIONS = [
    {"id": "20260523_162236_3f9fc329", "title": "Learning About Hermes Agent", "file": "transcript-a-session-20260523_162236_3f9fc329.txt"},
    {"id": "20260625_073100_29ae9591", "title": "Installing NOMAD on Apple Silicon", "file": "transcript-b-session-20260625_073100_29ae9591.txt"},
    {"id": "20260713_090633_f169c447", "title": "Codacus Video Memory Demo", "file": "transcript-c-truncated-20260713_090633_f169c447.txt"},
]

EXTRACTION_PROMPT = """You are a knowledge extraction agent. Given a conversation transcript, extract durable facts that would be useful across future sessions.

Extract:
1. ENTITIES — People, projects, tools, services with durable relevance
2. CONCEPTS — Techniques, patterns, principles, workflows
3. DECISIONS — Settled calls: what was chosen, what was rejected, why
4. PREFERENCES — User habits, conventions, env details, corrections
5. COMPARISONS — Trade-offs analyzed, side-by-side evaluations

IMPORTANT: Entity disambiguation. If the transcript mentions a name or place that could be ambiguous, include enough context in the summary to distinguish this from other references. If you're not sure two references are the same entity, extract them separately with a note.

Skip: greetings, one-off queries, chit-chat, transient state.

For each extraction, output a JSON object with these fields:
{
  "type": "entity|concept|comparison",
  "title": "Short Descriptive Title",
  "summary": "2-4 sentences explaining the fact and why it matters. Use [[wikilinks]] for cross-references.",
  "tags": ["memory", "tooling", "preference"],
  "sources": ["<session_id>"],
  "confidence": "high|medium|low"
}

- high: multiple strong signals, clear evidence in transcript
- medium: plausible but could be wrong — needs corroboration
- low: speculative, incomplete, or single weak mention

Output ONLY a JSON array, nothing else. One array entry per fact. If no facts found, output an empty array [].

Example:
[
  {
    "type": "entity",
    "title": "Alex Chen",
    "summary": "A frequent collaborator, based in San Francisco. Works on the data pipeline project.",
    "tags": ["person"],
    "sources": ["abc123"],
    "confidence": "high"
  }
]"""

MODELS = [
    {
        "name": "h4-14b",
        "base_url": "http://127.0.0.1:8000/v1",
        "model": "Hermes-4-14B-4bit",
        "api_key": "${LLM_API_KEY:-your-api-key}",
    },
    {
        "name": "gemma-4-e4b",
        "base_url": "http://127.0.0.1:8000/v1",
        "model": "gemma-4-E4B-it-qat-mxfp4",
        "api_key": "${LLM_API_KEY:-your-api-key}",
    },
]

GT_DIR = os.path.expanduser("~/.hermes/eval/ground-truth")
RAW_DIR = os.path.expanduser("~/.hermes/eval/raw")
OUT_DIR = os.path.expanduser("~/.hermes/eval")

def load_transcript(session_file):
    path = os.path.join(GT_DIR, session_file)
    with open(path) as f:
        return f.read()

def build_prompt(transcript, session_id, title):
    return (
        f"## Session: {title}\n"
        f"## Session ID: {session_id}\n\n"
        f"{transcript}\n\n"
        f"---\n\n"
        f"{EXTRACTION_PROMPT.replace('<session_id>', session_id)}"
    )

def call_llm(prompt, session_id, model_config):
    """Direct API call using urllib (same as session-to-wiki.py)."""
    os.environ["LLM_API_BASE_URL"] = model_config["base_url"]
    os.environ["LLM_API_KEY"] = model_config["api_key"]
    os.environ["LLM_MODEL"] = model_config["model"]
    
    # Use the script's function
    return call_llm_for_extraction(prompt, session_id)

def main():
    results = []
    
    for model in MODELS:
        print(f"\n{'='*60}")
        print(f"Model: {model['name']}")
        print(f"{'='*60}")
        
        for session in SESSIONS:
            print(f"\n  Session: {session['title']} ({session['id'][:8]})...")
            
            transcript = load_transcript(session["file"])
            prompt = build_prompt(transcript, session["id"], session["title"])
            
            start = time.time()
            facts = call_llm(prompt, session["id"], model)
            elapsed = time.time() - start
            
            # Save raw output
            raw_dir = os.path.join(RAW_DIR, model["name"])
            os.makedirs(raw_dir, exist_ok=True)
            raw_path = os.path.join(raw_dir, f"{session['id']}.json")
            with open(raw_path, "w") as f:
                json.dump({"session_id": session["id"], "model": model["name"], "facts": facts, "latency": elapsed, "fact_count": len(facts)}, f, indent=2)
            
            print(f"    {len(facts)} facts in {elapsed:.1f}s → saved to {raw_path}")
            
            results.append({
                "model": model["name"],
                "session": session["id"],
                "fact_count": len(facts),
                "latency": elapsed,
                "facts": facts,
            })
    
    # Summary
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    for r in results:
        print(f"  {r['model']:15s} | {r['session'][:8]} | {r['fact_count']:3d} facts | {r['latency']:5.1f}s")
    
    # Save combined results
    summary_path = os.path.join(OUT_DIR, "raw-results.json")
    with open(summary_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nFull results saved to {summary_path}")

if __name__ == "__main__":
    main()