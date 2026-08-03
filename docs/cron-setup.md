# Cron Setup

## Daily Extraction

The recommended setup runs extraction daily at 6am using the shell wrapper:

### Using `hermes cron` (Hermes Agent)

```bash
hermes cron create \
  --schedule "0 6 * * *" \
  --name "session-to-wiki" \
  --script "~/memento/scripts/wiki-extract-pipeline.sh" \
  --no-agent true \
  --deliver "local"
```

The `no_agent: true` flag makes the cron run the script directly (no LLM agent loop), which is cheaper and avoids context-limit issues. The script handles the LLM calls internally.

### Using system cron

```bash
# crontab -e
0 6 * * * cd ~/memento/scripts && ./wiki-extract-pipeline.sh 2>&1
```

### Using launchd (macOS)

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.user.memento-extraction</string>
    <key>ProgramArguments</key>
    <array>
        <string>~/memento/scripts/wiki-extract-pipeline.sh</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>6</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>
    <key>RunAtLoad</key>
    <false/>
</dict>
</plist>
```

## Pipeline Configuration

### wiki-extract-pipeline.sh

The default wrapper uses Qwen3.5-9B on a local oMLX server. Edit the env vars to match your setup:

```bash
export LLM_API_BASE_URL="http://127.0.0.1:8000/v1"
export LLM_API_KEY="your-api-key"
export LLM_MODEL="Qwen3.5-9B-MLX-4bit"
export LLM_ALLOW_THINKING="false"      # thinking models: prevents output-token exhaustion (finish_reason=length)
export LLM_MAX_TOKENS="8192"
export MEMENTO_TRANSCRIPT_BUDGET="50000"
```

### Performance Tuning

| `--max` | Sessions | Est. Time (9B model) |
|---------|----------|---------------------|
| 5 | 5 sessions | ~22 minutes |
| 10 | 10 sessions | ~43 minutes |
| 15 | 15 sessions | ~65 minutes |

Adjust `--max` in the pipeline script to fit your available window.

## Daily Curation (Phase 2 — v7.2+)

The curation pipeline (`wiki-compact.py`) runs AFTER extraction. It uses a small local LLM for classification tasks (contradiction detection, dedup decisions).

### Using `hermes cron`

```bash
hermes cron create \
  --schedule "0 7 * * *" \
  --name "wiki-curation" \
  --script "~/memento/scripts/wiki-curation.sh" \
  --no-agent true \
  --deliver "local"
```

### Curation wrapper script

Create `~/memento/scripts/wiki-curation.sh`:

```bash
#!/bin/bash
# Kick oMLX if not running (needed for LLM-based features)
launchctl kickstart -k gui/$(id -u)/com.omlx.server 2>/dev/null

# Wait for it
for i in $(seq 1 30); do
  curl -s http://127.0.0.1:8000/v1/models > /dev/null 2>&1 && break
  sleep 2
done

# Curation env
export LLM_API_BASE_URL="http://127.0.0.1:8000/v1"
export LLM_MODEL="gemma-4-E4B-it-qat-mxfp4"
export COMPACT_LLM_MAX_CALLS="40"

# Run (uses same extraction lock to avoid interleaving)
/usr/bin/python3 ~/.hermes/scripts/wiki-compact.py --llm-budget 40
```

### Running without oMLX (mechanical only)

```bash
/usr/bin/python3 wiki-compact.py --no-llm --dry-run
```

Mechanical features (broken links, orphan wiring, propagate flags) don't need an LLM. Set up a separate cron for this if you want a cheaper pre-check.

## Mutual Exclusion

The curation script uses the same `extraction` lock as the extraction pipeline. If extraction is still running when curation starts, it waits. No overlapping writes.

```bash
# Check if anything is running
./wiki-lock.sh check extraction

# Extraction cron
./wiki-lock.sh acquire extraction
python3 session-to-wiki.py --auto --max 10
./wiki-lock.sh release extraction

# Curation cron (waits if extraction still holds lock)
./wiki-lock.sh acquire extraction
python3 wiki-compact.py --llm-budget 40
./wiki-lock.sh release extraction
```