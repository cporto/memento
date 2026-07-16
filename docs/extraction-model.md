# Extraction Model Guide

## Recommended Models

The extraction pipeline is model-agnostic — any OpenAI-compatible API endpoint will work. Here are models tested with the Memento pipeline:

| Model | Context | Notes |
|-------|---------|-------|
| Qwen3.5-9B-4bit | 40K | **Recommended.** Good balance of speed and quality. ~17.9s per call, ~4m21s per session (4 calls). |
| Qwen3-8B-4bit | 40K | Faster than Qwen3.5-9B (~12.2s/call), slightly less detailed extractions. |
| Gemma-4-E4B-4bit | 131K | 4B MoE, handles long sessions. ~10-11 facts per session, 53-87s latency. |
| Hermes-4-14B-4bit | 40K | Higher quality but may OOM on long sessions with 16GB RAM. |
| DeepSeek V4 Flash (API) | — | Fastest option if you have API access. |

## Environment Variables

```bash
# Required
export LLM_API_BASE_URL="http://127.0.0.1:8000/v1"
export LLM_API_KEY="your-api-key"
export LLM_MODEL="Qwen3.5-9B-MLX-4bit"

# Optional
export LLM_ALLOW_THINKING="false"  # Suppress thinking tokens (Qwen3.5+, DeepSeek R1)
```

## Model Selection Tips

1. **Start with a small local model** (7-9B 4-bit) for daily cron — it's free and fast enough
2. **Use a larger model or API** for backfill or high-value sessions
3. **Suppress thinking tokens** for models that emit them — they add latency without benefit for extraction
4. **Test with `--max 1`** before scaling up to verify your model works with the prompt templates