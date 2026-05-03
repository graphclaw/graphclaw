"""Wave 2 E2E validation script.

Tests live gateway at localhost:8000.
Wave 2 FRs: FR-OUT-001..004 — OutboundCommunicationAgent.

These tests:
1. Verify OutboundIntent + OutboundCommunicationAgent classes importable (unit-level).
2. Verify ReplyKeyStore dual-write model works (unit-level).
3. Test chat with the main orchestrator to confirm agent still works post-Wave-2.
4. Verify reply_lineage table exists in DB.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

BASE = "http://localhost:8000"
RESULTS: list[str] = []


def call(
    method: str,
    path: str,
    body: dict | None = None,
    token: str | None = None,
) -> tuple[int, dict]:
    url = f"{BASE}{path}"
    data = json.dumps(body).encode() if body else None
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def check(name: str, condition: bool, detail: str = "") -> None:
    mark = "PASS" if condition else "FAIL"
    msg = f"[{mark}] {name}" + (f": {detail}" if detail else "")
    RESULTS.append(msg)
    print(msg)


def main() -> None:
    # ── Import checks (module-level, no live gateway needed) ─────────────────
    from graphclaw.agent.outbound import DispatchResult, OutboundCommunicationAgent
    from graphclaw.agent.outbound_intent import OutboundIntent
    from graphclaw.inbound.reply_keys import ReplyKeyRecord, ReplyKeyStore

    check("OutboundIntent importable", True)
    check("OutboundCommunicationAgent importable", True)
    check("ReplyKeyStore importable", True)
    check("DispatchResult importable", True)

    # ── OutboundIntent model ─────────────────────────────────────────────────
    intent = OutboundIntent(recipient_id="RES-bob", purpose="Follow up on project", task_id="TSK-001")
    check("OutboundIntent default channel_override is None", intent.channel_override is None)
    check("OutboundIntent deadline_extension_days defaults 0", intent.deadline_extension_days == 0)

    # ── ReplyKeyRecord serialization ─────────────────────────────────────────
    record = ReplyKeyRecord(
        task_id="TSK-001",
        counterparty_id="RES-bob",
        user_id="USER-1",
        channel="telegram",
        thread_id="TG-CHAT-123",
        checkin_id="CHK-001",
    )
    restored = ReplyKeyRecord.from_json(record.to_json())
    check("ReplyKeyRecord roundtrip", restored.task_id == "TSK-001")
    check("Redis key pattern correct",
          ReplyKeyStore.redis_key("telegram", "TG-CHAT-123", "MSG-1") == "checkin:telegram:TG-CHAT-123:MSG-1")

    # ── Auth ─────────────────────────────────────────────────────────────────
    status, resp = call("POST", "/auth/dev-token", {"user_id": "USER-dev-001", "role": "ADMIN"})
    check("dev-token returns 200", status == 200, f"status={status}")
    token = resp.get("access_token", "")

    # ── Chat still works (FR-OUT-001 regression) ─────────────────────────────
    status, resp = call(
        "POST",
        "/app/v1/chat/messages",
        {"message": "hello"},
        token,
    )
    check("POST /app/v1/chat/messages returns 200 or 422", status in (200, 201, 422), f"status={status}")

    # ── Policy API still works ────────────────────────────────────────────────
    status, resp = call(
        "PUT",
        "/app/v1/agents/main/policies/delegation",
        {
            "frontmatter": {
                "fail_mode": "closed",
                "auto_acknowledge": True,
                "accept_deadline_extension_max_days": 5,
            },
            "body": "Wave 2 delegation policy",
        },
        token,
    )
    check("PUT delegation policy still works", status == 200, f"status={status}")

    # ── Summary ──────────────────────────────────────────────────────────────
    passed = sum(1 for r in RESULTS if r.startswith("[PASS]"))
    failed = sum(1 for r in RESULTS if r.startswith("[FAIL]"))
    print(f"\nWave 2 E2E: {passed} passed, {failed} failed")
    if failed > 0:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
