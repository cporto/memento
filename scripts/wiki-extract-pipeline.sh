#!/bin/bash
# wiki-extract-pipeline.sh — Full extraction pipeline for cron.
# Uses local oMLX (Qwen3.5-9B-MLX-4bit) by default.
# Falls back to Fireworks (DeepSeek V4 Flash) if local model is unreachable.
# Uses --max 5 (~4min/session local, ~20min total). Local mode tuned for 16GB.
# Prevent macOS sleep during extraction with caffeinate.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

OMLX_KEY="${OMLX_API_KEY:-your-api-key}"

echo "[$(date)] Starting session-to-wiki auto extraction (local, max 5)..." >&2

# First, check if local oMLX is responsive
LOCAL_UP=false
if curl -sf --connect-timeout 3 http://127.0.0.1:8000/v1/models \
  -H "Authorization: Bearer $OMLX_KEY" >/dev/null 2>&1; then
  LOCAL_UP=true
fi

if [ "$LOCAL_UP" = true ]; then
  export LLM_API_BASE_URL="http://127.0.0.1:8000/v1"
  export LLM_API_KEY="$OMLX_KEY"
  export LLM_MODEL="Qwen3.5-9B-MLX-4bit"
  # Explicitly disable thinking: oMLX honors chat_template_kwargs
  # enable_thinking=false. Left on, thinking burns the output-token budget
  # (finish_reason=length) and adds ~20x latency per call.
  export LLM_ALLOW_THINKING="false"
  # Tuned for 16GB: 8192 tokens output budget, 50K char transcript chunks
  export LLM_MAX_TOKENS="8192"
  export MEMENTO_TRANSCRIPT_BUDGET="50000"
  echo "[$(date)] Using local oMLX (Qwen3.5-9B-MLX-4bit)" >&2
elif [ -n "${FIREWORKS_API_KEY:-}" ]; then
  # LLM_ALLOW_THINKING deliberately NOT set here: the extraction script only
  # sends chat_template_kwargs when the var is present, and cloud APIs may
  # reject that nonstandard field.
  export LLM_API_BASE_URL="https://api.fireworks.ai/inference/v1"
  export LLM_API_KEY="$FIREWORKS_API_KEY"
  export LLM_MODEL="accounts/fireworks/models/deepseek-v4-flash"
  echo "[$(date)] Local oMLX unreachable, falling back to Fireworks (DeepSeek V4 Flash)" >&2
else
  echo "[$(date)] ERROR: Local oMLX unreachable and FIREWORKS_API_KEY not set. Nothing to do." >&2
  exit 1
fi

# Run extraction (max 5 sessions — local is slower than Fireworks).
# || capture is required under set -e: a bare non-zero exit (v6 uses 2/3 for
# ingest-failed/partial) would abort the script here and skip the lint step.
EXIT_CODE=0
caffeinate -dim /usr/bin/python3 "$SCRIPT_DIR/session-to-wiki.py" --auto --max 5 2>&1 || EXIT_CODE=$?

# Post-extraction lint check
echo "[wiki-extract-pipeline] Extraction exit code: $EXIT_CODE" >&2
"$SCRIPT_DIR/wiki-lint.sh" 2>&1 || true
exit $EXIT_CODE
