#!/bin/bash
# wiki-extract-pipeline.sh — Full extraction pipeline for cron.
# Runs session-to-wiki.py in --auto mode with local Qwen3.5-9B on oMLX.
# Uses --max 10 based on real timing (4m21s/session, ~43min for 10).
# Prevent macOS sleep during extraction with caffeinate.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "[$(date)] Starting session-to-wiki auto extraction (Qwen3.5-9B, max 10)..." >&2

export LLM_API_BASE_URL="http://127.0.0.1:8000/v1"
export LLM_API_KEY="${LLM_API_KEY:-your-api-key}"
export LLM_MODEL="Qwen3.5-9B-MLX-4bit"
export LLM_ALLOW_THINKING="false"

# Run extraction
caffeinate -dim /usr/bin/python3 "$SCRIPT_DIR/session-to-wiki.py" --auto --max 10 2>&1
EXIT_CODE=$?

# Post-extraction lint check
echo "[wiki-extract-pipeline] Extraction exit code: $EXIT_CODE" >&2
"$SCRIPT_DIR/wiki-lint.sh" 2>&1 || true
exit $EXIT_CODE