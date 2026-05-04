---
name: graphclaw-test-scripts
description: Conventions for scripts/ development and API smoke testing in GraphClaw. Use when writing files in scripts/ that touch the API, CLI, or orchestrator chat. Enforces the rule that scripts are NOT tests and must NEVER be wired into CI.
---

# GraphClaw Test Scripts (Dev Tooling)

## When to use
Writing or modifying files under `graphclaw/scripts/` that interact with the API, CLI, or chat — specifically `api_smoke.py` and `chat_repl.py`.

---

## The fundamental rule

> **Scripts are NOT tests.**

| Property | pytest integration test | Script in scripts/ |
|---|---|---|
| Has assertions | Yes | No — print output only |
| Fails the build | Yes | No — never in CI |
| Has fixtures | Yes | No — assumes seed data exists |
| Has structured reporting | Yes | No — stdout is the report |
| Runs on PR | Yes | No |

If you find yourself adding `assert` statements to a script, stop and promote it:
1. Move logic to `tests/integration/test_<name>.py`
2. Wrap in pytest fixtures
3. Allocate a test ID and write the header
4. Delete the original script

---

## api_smoke.py pattern

```python
#!/usr/bin/env python3
"""
Human smoke test for the GraphClaw API.

Usage:
    docker compose up -d
    python scripts/api_smoke.py

NOT a test — no assertions. For CI testing see tests/integration/.
"""
import httpx
import json

BASE = "http://localhost:8000"

def main():
    with httpx.Client(base_url=BASE, timeout=10) as c:
        # Auth
        r = c.post("/auth/dev-token", json={"email": "dev@example.com"})
        token = r.json()["access_token"]
        h = {"Authorization": f"Bearer {token}"}

        print("== /health ==")
        print(json.dumps(c.get("/health").json(), indent=2))

        print("\n== GET /app/v1/tasks ==")
        r = c.get("/app/v1/tasks", headers=h)
        print(f"Status: {r.status_code}, Count: {len(r.json().get('items', []))}")

        # Add more endpoints to inspect as needed
        # DO NOT add assert statements here

if __name__ == "__main__":
    main()
```

---

## chat_repl.py pattern

```python
#!/usr/bin/env python3
"""
Interactive REPL for testing the orchestrator agent.

Usage:
    docker compose up -d
    python scripts/chat_repl.py

NOT a test — no assertions. For automated agent testing see tests/agent_evals/.
"""
import asyncio
from graphclaw.agent.session import ChatSession

async def main():
    session = ChatSession(user_id="dev-user")
    print("Chat with Betty (Ctrl+C to exit)\n")
    while True:
        user_input = input("You: ").strip()
        if not user_input:
            continue
        response = await session.send(user_input)
        print(f"Betty: {response}\n")

if __name__ == "__main__":
    asyncio.run(main())
```

---

## Scripts must NOT be added to CI

If you find a script being called from `.github/workflows/`, a `Makefile` CI target, or `docker-compose.yml` as a service — remove it and open a ticket to promote it to a proper pytest test.

---

## When a script earns promotion to a test

Signs a script should be promoted:
- You run it before every release
- Others ask "did the smoke test pass?"
- You've added conditional checks to it

Promotion steps:
1. Copy the HTTP calls to `tests/integration/test_<name>.py`
2. Replace `print(r.json())` with `assert r.status_code == 200` etc.
3. Wrap in `@pytest.mark.integration` and add fixtures
4. Write the file header, allocate a test ID
5. Delete the script
