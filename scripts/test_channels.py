"""scripts/test_channels.py — Smoke-test email and Telegram channel connectivity.

Run this BEFORE starting the gateway to verify credentials are correct.

Usage:
    cd C:/Users/abhis/Projects/graphclaw
    python scripts/test_channels.py

Tests:
    1. Gmail IMAP login (checks App Password is correct)
    2. Gmail SMTP login (checks sending works)
    3. Telegram bot getMe (checks bot token is valid)
"""
from __future__ import annotations

import asyncio
import imaplib
import os
import smtplib
import sys
from pathlib import Path


# ── Load docker/.env ──────────────────────────────────────────────────────────
env_file = Path(__file__).parent.parent / "docker" / ".env"
if env_file.exists():
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())

src = str(Path(__file__).parent.parent / "src")
if src not in sys.path:
    sys.path.insert(0, src)

# ── Results ───────────────────────────────────────────────────────────────────
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    status = "PASS" if ok else "FAIL"
    results.append((name, ok, detail))
    symbol = "✓" if ok else "✗"
    print(f"  [{status}] {symbol} {name}{': ' + detail if detail else ''}")


# ── 1. Gmail IMAP ─────────────────────────────────────────────────────────────
print("\n── Gmail IMAP ──────────────────────────────────────────────────────────")
imap_host = os.environ.get("GATEWAY_IMAP_HOST", "")
imap_user = os.environ.get("GATEWAY_IMAP_USER", "")
imap_pass = os.environ.get("GATEWAY_IMAP_PASS", "")
imap_port = int(os.environ.get("GATEWAY_IMAP_PORT", "993"))

if not all([imap_host, imap_user, imap_pass]):
    check("IMAP credentials", False, "GATEWAY_IMAP_HOST/USER/PASS not set")
elif imap_pass == "REPLACE_WITH_GMAIL_APP_PASSWORD":
    check("IMAP App Password", False, "Still using placeholder — see instructions below")
else:
    try:
        with imaplib.IMAP4_SSL(imap_host, imap_port) as conn:
            conn.login(imap_user, imap_pass)
            status, data = conn.select("INBOX")
            msg_count = int(data[0]) if data[0] else 0
            check("IMAP login", True, f"INBOX has {msg_count} messages")
            conn.logout()
    except imaplib.IMAP4.error as e:
        check("IMAP login", False, str(e))
    except Exception as e:
        check("IMAP login", False, f"{type(e).__name__}: {e}")

# ── 2. Gmail SMTP ─────────────────────────────────────────────────────────────
print("\n── Gmail SMTP ──────────────────────────────────────────────────────────")
smtp_host = os.environ.get("GATEWAY_SMTP_HOST", "")
smtp_port = int(os.environ.get("GATEWAY_SMTP_PORT", "587"))

if not all([smtp_host, imap_user, imap_pass]):
    check("SMTP credentials", False, "GATEWAY_SMTP_HOST not set")
elif imap_pass == "REPLACE_WITH_GMAIL_APP_PASSWORD":
    check("SMTP App Password", False, "Still using placeholder — see instructions below")
else:
    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as server:
            server.ehlo()
            server.starttls()
            server.login(imap_user, imap_pass)
            check("SMTP login", True, f"{smtp_host}:{smtp_port}")
    except smtplib.SMTPAuthenticationError as e:
        check("SMTP login", False, f"Auth failed: {e}")
    except Exception as e:
        check("SMTP login", False, f"{type(e).__name__}: {e}")

# ── 3. Telegram Bot ───────────────────────────────────────────────────────────
print("\n── Telegram Bot ────────────────────────────────────────────────────────")
bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")

if not bot_token:
    check("Telegram bot token", False, "TELEGRAM_BOT_TOKEN not set")
else:
    async def _check_telegram():
        try:
            import httpx  # noqa: PLC0415
            url = f"https://api.telegram.org/bot{bot_token}/getMe"
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url)
                if resp.is_success:
                    data = resp.json()
                    bot = data.get("result", {})
                    name = bot.get("first_name", "?")
                    username = bot.get("username", "?")
                    check("Telegram getMe", True, f"Bot: {name} (@{username})")
                else:
                    check("Telegram getMe", False, f"HTTP {resp.status_code}: {resp.text[:100]}")
        except ImportError:
            check("Telegram httpx", False, "httpx not installed — run: pip install httpx")
        except Exception as e:
            check("Telegram getMe", False, f"{type(e).__name__}: {e}")

    asyncio.run(_check_telegram())

# ── Summary ───────────────────────────────────────────────────────────────────
print("\n── Summary ─────────────────────────────────────────────────────────────")
passed = sum(1 for _, ok, _ in results if ok)
total = len(results)
print(f"  {passed}/{total} checks passed")

needs_app_pass = any(
    "App Password" in name or "App Password" in detail
    for name, _, detail in results
)

if needs_app_pass or (imap_pass == "REPLACE_WITH_GMAIL_APP_PASSWORD"):
    print("""
╔══════════════════════════════════════════════════════════════════╗
║  Gmail App Password Required                                     ║
╠══════════════════════════════════════════════════════════════════╣
║  Google disabled basic IMAP auth in 2022.                        ║
║  Steps to create an App Password:                                ║
║                                                                  ║
║  1. Go to: https://myaccount.google.com/security                 ║
║  2. Enable "2-Step Verification" (required)                      ║
║  3. Search for "App passwords" in the search bar                 ║
║  4. Create one: App = Mail, Device = Other (type "GraphClaw")    ║
║  5. Copy the 16-character password shown (no spaces)             ║
║  6. Paste it into docker/.env as GATEWAY_IMAP_PASS=...           ║
║                                                                  ║
║  The app password looks like: abcd efgh ijkl mnop                ║
║  Enter it WITHOUT spaces: abcdefghijklmnop                       ║
╚══════════════════════════════════════════════════════════════════╝
""")

if passed == total:
    print("\n  All checks passed! Run the gateway with:")
    print("  python scripts/run_gateway_local.py")
