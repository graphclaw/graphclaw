#!/usr/bin/env python3
"""Wave 4 End-to-End tests: FR-CA-001, FR-CA-002, FR-CA-003.

Tests:
1. process_chat_message has channel + thread_id params
2. process_chat_message_stream has channel + thread_id params
3. process_counterparty_turn exists + is coroutine
4. COUNTERPARTY_ALLOWED_TOOL_NAMES defined
5. counterparty mode filters out delegate_to_agent
6. counterparty mode keeps get_task_details
7. DistillationHelper importable
8. DistillationInput/Result importable
9. Distillation noop when null extraction
10. Distillation writes node intelligence when task_entry provided
11. dev-token regression
12. Chat API still works (channel-agnostic regression)
13. admin/agent-channels GET still works (Wave 3 regression)
"""

from __future__ import annotations

import asyncio
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


# ── FR-CA-001: Channel-agnostic chat handler signature ────────────────────────

try:
    import inspect

    from graphclaw.agent.main_orchestrator import MainOrchestrator

    sig = inspect.signature(MainOrchestrator.process_chat_message)
    params = list(sig.parameters.keys())
    check("process_chat_message has 'channel' param", "channel" in params)
    check("process_chat_message has 'thread_id' param", "thread_id" in params)
    check(
        "process_chat_message 'channel' defaults to 'cockpit'",
        sig.parameters["channel"].default == "cockpit",
    )
except Exception as exc:
    check("process_chat_message signature", False, str(exc))
    check("process_chat_message has 'thread_id' param", False, str(exc))
    check("process_chat_message 'channel' defaults to 'cockpit'", False, str(exc))

try:
    sig2 = inspect.signature(MainOrchestrator.process_chat_message_stream)
    params2 = list(sig2.parameters.keys())
    check("process_chat_message_stream has 'channel' param", "channel" in params2)
    check("process_chat_message_stream has 'thread_id' param", "thread_id" in params2)
except Exception as exc:
    check("process_chat_message_stream has 'channel' param", False, str(exc))
    check("process_chat_message_stream has 'thread_id' param", False, str(exc))

try:
    check(
        "process_counterparty_turn exists + is coroutine",
        hasattr(MainOrchestrator, "process_counterparty_turn")
        and asyncio.iscoroutinefunction(MainOrchestrator.process_counterparty_turn),
    )
except Exception as exc:
    check("process_counterparty_turn exists + is coroutine", False, str(exc))

# ── FR-CA-003: Tool gating ─────────────────────────────────────────────────────

try:
    from graphclaw.agent.tool_registry import COUNTERPARTY_ALLOWED_TOOL_NAMES, ToolSetRegistry

    check("COUNTERPARTY_ALLOWED_TOOL_NAMES defined", len(COUNTERPARTY_ALLOWED_TOOL_NAMES) > 0)

    reg = ToolSetRegistry()
    reg.activate("delegation")
    tools_cp = reg.get_active_tools(mode="counterparty_conversation")
    names_cp = {t.name for t in tools_cp}
    check(
        "counterparty mode filters out delegate_to_agent",
        "delegate_to_agent" not in names_cp,
        str(names_cp),
    )
    check(
        "counterparty mode keeps get_task_details",
        "get_task_details" in names_cp,
        str(names_cp),
    )
    tools_default = reg.get_active_tools()
    names_default = {t.name for t in tools_default}
    check(
        "default mode keeps delegate_to_agent",
        "delegate_to_agent" in names_default,
        str(names_default),
    )
except Exception as exc:
    check("COUNTERPARTY_ALLOWED_TOOL_NAMES defined", False, str(exc))
    check("counterparty mode filters out delegate_to_agent", False, str(exc))
    check("counterparty mode keeps get_task_details", False, str(exc))
    check("default mode keeps delegate_to_agent", False, str(exc))

# ── FR-CA-002: DistillationHelper ─────────────────────────────────────────────

try:
    from graphclaw.agent.distillation import DistillationHelper, DistillationInput, DistillationResult

    check("DistillationHelper importable", True)
    check("DistillationInput importable", True)
    check("DistillationResult importable", True)
except Exception as exc:
    check("DistillationHelper importable", False, str(exc))
    check("DistillationInput importable", False, str(exc))
    check("DistillationResult importable", False, str(exc))

try:
    class FakeLLM:
        async def complete(self, *a, **kw):
            m = type("R", (), {"content": '{"task_entry": null, "memory_note": null}', "tool_calls": [], "usage": None})()
            return m

    class FakeRepo:
        async def get_node_intelligence(self, *a): return None
        async def update_node_intelligence(self, *a): pass

    class FakeStorage:
        _data = {}
        async def read(self, p):
            if p not in self._data: raise FileNotFoundError(p)
            return self._data[p]
        async def write(self, p, d, **kw): self._data[p] = d

    helper = DistillationHelper(llm=FakeLLM(), graph_repo=FakeRepo(), storage=FakeStorage())
    inp = DistillationInput(user_id="U1", agent_id="A1", user_text="hi", agent_reply="hey")
    result = asyncio.run(helper.distill(inp))
    check("Distillation noop when null extraction", result.action_taken == "noop", str(result))
except Exception as exc:
    check("Distillation noop when null extraction", False, str(exc))

try:
    class FakeLLM2:
        async def complete(self, *a, **kw):
            m = type("R", (), {"content": '{"task_entry": "test entry", "memory_note": null}', "tool_calls": [], "usage": None})()
            return m

    repo2 = FakeRepo()
    helper2 = DistillationHelper(llm=FakeLLM2(), graph_repo=repo2, storage=FakeStorage())
    inp2 = DistillationInput(user_id="U1", agent_id="A1", user_text="About TSK-1", agent_reply="TSK-1 done", task_id="TSK-1")
    result2 = asyncio.run(helper2.distill(inp2))
    check(
        "Distillation writes node intelligence",
        result2.action_taken in ("node_updated", "both"),
        str(result2),
    )
except Exception as exc:
    check("Distillation writes node intelligence", False, str(exc))

# ── HTTP regressions ─────────────────────────────────────────────────────────

import time as _time
_time.sleep(2)  # Give gateway time to restart

status, resp = call("POST", "/auth/dev-token", {"user_id": "USER-dev-001", "role": "ADMIN"})
check("dev-token regression", status == 200, f"status={status}")
token = resp.get("access_token", "")

status, _ = call("POST", "/app/v1/chat/messages", {"message": "hello wave4"}, token)
check("Chat API still works", status in (200, 201, 422), f"status={status}")

status, resp = call("GET", "/app/v1/admin/agent-channels", token=token)
check("Wave 3 admin/agent-channels still works", status == 200 and isinstance(resp, list), f"status={status}")

# ── Summary ────────────────────────────────────────────────────────────────────

print(f"\nWave 4 E2E: {PASS} passed, {FAIL} failed")
sys.exit(0 if FAIL == 0 else 1)
