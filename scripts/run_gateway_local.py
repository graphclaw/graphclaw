"""scripts/run_gateway_local.py — Run the GraphClaw gateway locally without Docker.

Sets environment variables from docker/.env and starts uvicorn so you can test
email + Telegram channels without spinning up the full Docker stack.

Usage:
    cd C:/Users/abhis/Projects/graphclaw
    python scripts/run_gateway_local.py

Requirements (must be running locally):
    - Redis: docker run -p 6379:6379 redis:7-alpine
      OR: winget install Redis.Redis (then redis-server)

If Redis is not available the gateway still starts but runs in degraded mode
(messages are logged but not queued).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# ── Load docker/.env into os.environ ─────────────────────────────────────────
env_file = Path(__file__).parent.parent / "docker" / ".env"
if env_file.exists():
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        # Don't overwrite values already set in the shell environment
        if key.strip() not in os.environ:
            os.environ[key.strip()] = value.strip()
    print(f"Loaded env from {env_file}")
else:
    print(f"Warning: {env_file} not found — using shell environment only")

# Override REDIS_URL to local (not Docker internal hostname)
os.environ.setdefault("REDIS_URL", "redis://localhost:6379")

# Add src/ to path so graphclaw is importable without installing
src = str(Path(__file__).parent.parent / "src")
if src not in sys.path:
    sys.path.insert(0, src)

# ── Print active config (without secrets) ────────────────────────────────────
print("\n=== GraphClaw Gateway — Local Test Mode ===")
print(f"  Channels   : {os.environ.get('GATEWAY_ENABLED_CHANNELS', 'email')}")
print(f"  Redis URL  : {os.environ.get('REDIS_URL', '(not set)')}")
print(f"  IMAP host  : {os.environ.get('GATEWAY_IMAP_HOST', '(not set)')}")
print(f"  IMAP user  : {os.environ.get('GATEWAY_IMAP_USER', '(not set)')}")
print(f"  IMAP pass  : {'(set)' if os.environ.get('GATEWAY_IMAP_PASS') else '(NOT SET)'}")
print(f"  Telegram   : {'(token set)' if os.environ.get('TELEGRAM_BOT_TOKEN') else '(NOT SET)'}")
print("============================================\n")

# ── Start uvicorn ─────────────────────────────────────────────────────────────
import uvicorn  # noqa: E402

uvicorn.run(
    "graphclaw.gateway.server:app",
    host="0.0.0.0",
    port=8000,
    reload=True,
    reload_dirs=[src],
    log_level="info",
)
