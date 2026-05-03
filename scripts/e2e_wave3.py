#!/usr/bin/env python3
"""Wave 3 End-to-End tests: FR-IN-001..003.

Tests:
1. InboundRouter importable
2. InboundRoute enum values
3. RouteDecision importable
4. AgentChannelIdentityRegistry importable
5. AgentChannelIdentity model importable and defaults valid
6. AliasResolver.resolve_to_node importable
7. Admin agent-channels API: GET returns 200 list
8. Admin agent-channels API: POST creates entry
9. Admin agent-channels API: GET after POST returns entry
10. Admin agent-channels API: PUT updates entry
11. Admin agent-channels API: DELETE deactivates entry (soft)
12. dev-token still works (regression)
13. InboundRouter classify drop route
14. InboundRouter classify user_chat route (in-process unit check)
"""

from __future__ import annotations

import sys

import requests

BASE = "http://localhost:8000"

PASS = 0
FAIL = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"[PASS] {label}")
    else:
        FAIL += 1
        print(f"[FAIL] {label}" + (f": {detail}" if detail else ""))


def call(method: str, path: str, body=None, token: str = "") -> tuple[int, dict]:
    url = f"{BASE}{path}"
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    resp = requests.request(method, url, json=body, headers=headers, timeout=15)
    try:
        data = resp.json()
    except Exception:
        data = {}
    return resp.status_code, data


# ── Import checks ─────────────────────────────────────────────────────────────

try:
    from graphclaw.inbound.router import InboundRoute, InboundRouter, RouteDecision  # noqa: F401

    check("InboundRouter importable", True)
except Exception as exc:
    check("InboundRouter importable", False, str(exc))

try:
    assert InboundRoute.USER_CHAT == "user_chat"
    assert InboundRoute.COUNTERPARTY_REPLY == "counterparty_reply"
    assert InboundRoute.COUNTERPARTY_PROACTIVE == "counterparty_proactive"
    assert InboundRoute.UNKNOWN_PARTY == "unknown_party"
    assert InboundRoute.DROP == "drop"
    check("InboundRoute enum values correct", True)
except Exception as exc:
    check("InboundRoute enum values correct", False, str(exc))

try:
    rd = RouteDecision(route=InboundRoute.DROP)
    check("RouteDecision instantiable with defaults", rd.owner_user_id == "")
except Exception as exc:
    check("RouteDecision instantiable with defaults", False, str(exc))

try:
    from graphclaw.gateway.agent_channel_identity import AgentChannelIdentityRegistry  # noqa: F401

    check("AgentChannelIdentityRegistry importable", True)
except Exception as exc:
    check("AgentChannelIdentityRegistry importable", False, str(exc))

try:
    from graphclaw.models.agent_channel_identity import AgentChannelIdentity

    entry = AgentChannelIdentity(
        user_id="U1", agent_id="A1", channel="telegram", account_id="bot1"
    )
    check(
        "AgentChannelIdentity defaults valid",
        entry.active is True and entry.owner_identities == [],
    )
except Exception as exc:
    check("AgentChannelIdentity defaults valid", False, str(exc))

try:
    from graphclaw.gateway.alias_resolver import AliasResolver  # noqa: F401

    check("AliasResolver.resolve_to_node importable", hasattr(AliasResolver, "resolve_to_node"))
except Exception as exc:
    check("AliasResolver.resolve_to_node importable", False, str(exc))

# ── In-process routing checks ─────────────────────────────────────────────────

try:
    import asyncio

    from graphclaw.gateway.agent_channel_identity import AgentChannelIdentityRegistry
    from graphclaw.models.agent_channel_identity import AgentChannelIdentity

    e = AgentChannelIdentity(
        user_id="U1",
        agent_id="A1",
        channel="telegram",
        account_id="bot1",
        owner_identities=["owner_tg"],
        active=True,
    )
    reg = AgentChannelIdentityRegistry([e])
    router = InboundRouter(channel_registry=reg)
    decision = asyncio.run(
        router.classify(channel="telegram", sender_id="stranger", receiving_account="no_bot")
    )
    check("InboundRouter classify DROP for unknown account", decision.route == InboundRoute.DROP)
except Exception as exc:
    check("InboundRouter classify DROP for unknown account", False, str(exc))

try:
    decision2 = asyncio.run(
        router.classify(channel="telegram", sender_id="owner_tg", receiving_account="bot1")
    )
    check(
        "InboundRouter classify USER_CHAT for owner sender",
        decision2.route == InboundRoute.USER_CHAT,
    )
except Exception as exc:
    check("InboundRouter classify USER_CHAT for owner sender", False, str(exc))

# ── HTTP: dev-token (regression) ─────────────────────────────────────────────

status, resp = call("POST", "/auth/dev-token", {"user_id": "USER-dev-001", "role": "ADMIN"})
check("dev-token returns 200", status == 200, f"status={status}")
token = resp.get("access_token", "")

# ── HTTP: admin/agent-channels CRUD ──────────────────────────────────────────

# GET list (empty is fine)
status, resp = call("GET", "/app/v1/admin/agent-channels", token=token)
check(
    "GET /admin/agent-channels returns 200 list",
    status == 200 and isinstance(resp, list),
    f"status={status}",
)

# POST create
body = {
    "user_id": "USER-dev-001",
    "agent_id": "AGENT-dev-001",
    "channel": "telegram",
    "account_id": "e2e_bot_w3",
    "display_name": "Wave3 E2E Bot",
    "credentials_ref": "tg_bot_token_dev",
    "active": True,
    "owner_identities": ["dev_owner_tg_id"],
}
status, resp = call("POST", "/app/v1/admin/agent-channels", body, token)
check(
    "POST /admin/agent-channels returns 201",
    status == 201,
    f"status={status} resp={resp}",
)

# GET after POST
status, resp = call("GET", "/app/v1/admin/agent-channels", token=token)
entries = resp if isinstance(resp, list) else []
found = any(e.get("account_id") == "e2e_bot_w3" for e in entries)
check("GET after POST contains created entry", found, f"entries={entries}")

# PUT update
put_body = {**body, "display_name": "Updated Wave3 Bot"}
status, resp = call(
    "PUT", "/app/v1/admin/agent-channels/telegram/e2e_bot_w3", put_body, token
)
check(
    "PUT /admin/agent-channels updates entry",
    status == 200 and resp.get("display_name") == "Updated Wave3 Bot",
    f"status={status} resp={resp}",
)

# DELETE (deactivate)
status, _ = call("DELETE", "/app/v1/admin/agent-channels/telegram/e2e_bot_w3", token=token)
check(
    "DELETE /admin/agent-channels returns 204",
    status == 204,
    f"status={status}",
)

# GET after DELETE — entry should be present but inactive
status, resp = call("GET", "/app/v1/admin/agent-channels", token=token)
entries = resp if isinstance(resp, list) else []
deactivated = next((e for e in entries if e.get("account_id") == "e2e_bot_w3"), None)
check(
    "Entry still present but inactive after DELETE",
    deactivated is not None and deactivated.get("active") is False,
    f"deactivated={deactivated}",
)

# ── Summary ────────────────────────────────────────────────────────────────────

print(f"\nWave 3 E2E: {PASS} passed, {FAIL} failed")
sys.exit(0 if FAIL == 0 else 1)
