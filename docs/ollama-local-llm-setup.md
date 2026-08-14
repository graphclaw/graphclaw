# Ollama Local LLM Setup Guide

## Overview

GraphClaw supports using Ollama for cost-free local LLM development. This guide explains how to configure GraphClaw to use your local Ollama instance instead of cloud-based LLM providers.

## Benefits

- **Zero API costs** during development
- **Faster iteration** (no network latency)
- **Privacy** — all LLM calls stay on your machine
- **Offline development** — no internet required

## Prerequisites

1. **Ollama installed and running**
   ```bash
   # Install Ollama (macOS/Linux)
   curl https://ollama.ai/install.sh | sh
   
   # Start Ollama server
   ollama serve
   ```

2. **Pull a model**
   ```bash
   # Recommended models for GraphClaw development:
   
   # Small & fast (3B params, ~2GB)
   ollama pull llama3.2
   
   # Better code generation (14B params, ~9GB)
   ollama pull qwen2.5-coder:14b
   
   # Best reasoning (32B params, ~20GB)
   ollama pull qwen2.5-coder:32b
   ```

3. **Verify Ollama is running**
   ```bash
   curl http://localhost:11434/api/tags
   ```

## Configuration

### Option A: Environment Variables (Recommended)

Edit `docker/.env`:

```bash
# Switch to LiteLLM provider (routes to any LLM via model prefix)
GRAPHCLAW_DEFAULT_LLM_PROVIDER=litellm

# Set Ollama base URL
OLLAMA_API_BASE=http://localhost:11434

# Set default Ollama model
OLLAMA_DEFAULT_MODEL=llama3.2

# Configure LiteLLM to use Ollama by default
LITELLM_DEFAULT_MODEL=ollama/llama3.2
```

### Option B: Per-Request Model Selection

You can also specify the model at call time without changing defaults:

```python
from graphclaw.llm import create_llm_client, LLMMessage

# Create client with Ollama model
client = create_llm_client("litellm", default_model="ollama/qwen2.5-coder")

response = await client.complete([
    LLMMessage(role="user", content="Write a Python function to reverse a string")
])

print(response.content)
await client.close()
```

## Environment Variables Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `OLLAMA_API_BASE` | `http://localhost:11434` | Ollama server URL |
| `OLLAMA_DEFAULT_MODEL` | `llama3.2` | Default Ollama model name (without `ollama/` prefix) |
| `LITELLM_DEFAULT_MODEL` | `claude-sonnet-4-20250514` | LiteLLM default model (use `ollama/<model>` for Ollama) |
| `GRAPHCLAW_DEFAULT_LLM_PROVIDER` | `anthropic` | Default provider (`anthropic` \| `openai` \| `litellm`) |

## Model Prefix Format

LiteLLM uses model prefixes to route to different providers:

```python
# Anthropic (cloud, billable)
"anthropic/claude-sonnet-4-20250514"

# OpenAI (cloud, billable)
"openai/gpt-4o"

# Ollama (local, free)
"ollama/llama3.2"
"ollama/qwen2.5-coder:14b"
"ollama/deepseek-r1"
```

## Testing the Integration

### Manual Test

```bash
# Make sure Ollama is running
ollama serve

# Pull a model if you haven't
ollama pull llama3.2

# Run the integration test
cd /path/to/graphclaw
python3 scripts/test_ollama_integration.py
```

### Quick CLI Test

```bash
# Start GraphClaw CLI with Ollama
export LITELLM_DEFAULT_MODEL=ollama/llama3.2
export GRAPHCLAW_DEFAULT_LLM_PROVIDER=litellm

python -m graphclaw.cli chat
```

## Docker Configuration

### Using Docker Host Network (macOS/Windows)

When running GraphClaw in Docker, use `host.docker.internal` to reach Ollama on the host:

```bash
# In docker/.env
OLLAMA_API_BASE=http://host.docker.internal:11434
```

### Running Ollama in Docker (Optional)

Alternatively, add Ollama to `docker-compose.yml`:

```yaml
services:
  ollama:
    image: ollama/ollama:latest
    ports:
      - "11434:11434"
    volumes:
      - ollama_data:/root/.ollama
    networks:
      - graphclaw

volumes:
  ollama_data:
```

Then set:
```bash
OLLAMA_API_BASE=http://ollama:11434
```

## Recommended Models

| Model | Size | Best For | Memory Required |
|-------|------|----------|-----------------|
| `llama3.2` | 3B | Quick tests, simple queries | 4GB RAM |
| `qwen2.5-coder:7b` | 7B | Code generation, reviews | 8GB RAM |
| `qwen2.5-coder:14b` | 14B | Complex code tasks | 16GB RAM |
| `qwen2.5-coder:32b` | 32B | Production-quality code | 32GB RAM |
| `deepseek-r1:8b` | 8B | Reasoning, planning | 10GB RAM |

## Switching Between Cloud and Local

### During Development

Use Ollama for most development work:

```bash
# .env for local dev
LITELLM_DEFAULT_MODEL=ollama/llama3.2
```

### For Testing Cloud Parity

Switch to Anthropic/OpenAI for final validation:

```bash
# .env for cloud testing
LITELLM_DEFAULT_MODEL=anthropic/claude-sonnet-4-20250514
# OR
LITELLM_DEFAULT_MODEL=openai/gpt-4o
```

### Hybrid Approach

Use Ollama for skill agents (cheap tasks) and Anthropic for the main orchestrator (complex tasks):

```python
# Main orchestrator uses Anthropic
main_client = create_llm_client("anthropic", default_model="claude-sonnet-4-6")

# Skill agents use Ollama
skill_client = create_llm_client("litellm", default_model="ollama/llama3.2")
```

## Troubleshooting

### "Cannot connect to Ollama"

1. Check Ollama is running: `ps aux | grep ollama`
2. Start if not: `ollama serve`
3. Verify endpoint: `curl http://localhost:11434/api/tags`

### "Model not found"

Pull the model first:
```bash
ollama pull llama3.2
```

### Slow responses

- Try a smaller model (llama3.2 is fastest)
- Check CPU/GPU usage
- Ensure sufficient RAM

### Docker connectivity issues (Mac/Windows)

Use `host.docker.internal` instead of `localhost`:
```bash
OLLAMA_API_BASE=http://host.docker.internal:11434
```

## Cost Comparison

| Provider | Input (1M tokens) | Output (1M tokens) | Ollama |
|----------|------------------|-------------------|--------|
| Claude Sonnet 4 | $3.00 | $15.00 | **$0.00** |
| GPT-4o | $2.50 | $10.00 | **$0.00** |
| Ollama (local) | **$0.00** | **$0.00** | **$0.00** |

**Estimated savings during development:** 100% of LLM API costs

## Architecture Details

The implementation uses the existing factory pattern without requiring any new provider class:

1. `AppConfig` reads Ollama settings from environment
2. `LiteLLMLLMClient` auto-configures `api_base` for `ollama/` models
3. `LLMRouter` uses configurable default model from `AppConfig`
4. All business logic remains unchanged (uses `LLMClient` ABC)

This is **Option A** from the design — leveraging LiteLLM's built-in Ollama support rather than creating a dedicated `OllamaLLMClient` class.

## Next Steps

1. Configure your `.env` file with Ollama settings
2. Pull your preferred model(s)
3. Run the integration test script
4. Start developing without worrying about API costs! 🎉
