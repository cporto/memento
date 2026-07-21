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

## Mutual Exclusion

If you run multiple wiki-related cron jobs (extraction + curation), use `wiki-lock.sh` to prevent overlapping runs:

```bash
# Check if extraction is running
./wiki-lock.sh check extraction

# Extraction cron
./wiki-lock.sh acquire extraction
python3 session-to-wiki.py --auto --max 10
./wiki-lock.sh release extraction
```