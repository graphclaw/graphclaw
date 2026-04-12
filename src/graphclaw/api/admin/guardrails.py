"""graphclaw.api.admin.guardrails — XML guardrail rules management endpoints.

Routes
------
GET  /app/v1/admin/guardrails            — get current guardrail rules
PUT  /app/v1/admin/guardrails            — replace guardrail rules
POST /app/v1/admin/guardrails/validate   — validate a rule set without saving
POST /app/v1/admin/guardrails/test       — test a message against the rules
GET  /app/v1/admin/guardrails/metrics    — guardrail trigger metrics
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, status
from pydantic import BaseModel

from graphclaw.api.deps import AdminUserDep, StorageClientDep

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/guardrails", tags=["admin-api"])

_RULES_PATH = "admin/guardrails/rules.json"
_METRICS_PATH = "admin/guardrails/metrics.json"


async def _load_json(path: str, storage_client: Any, default: Any = None) -> Any:
    try:
        raw = await storage_client.read(path)
        return json.loads(raw.decode())
    except FileNotFoundError:
        return default if default is not None else {}


async def _save_json(path: str, storage_client: Any, data: Any) -> None:
    await storage_client.write(path, json.dumps(data).encode(), content_type="application/json")


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class GuardrailRule(BaseModel):
    """A single guardrail rule."""

    rule_id: str
    name: str
    pattern: str
    action: str = "BLOCK"  # BLOCK | WARN | LOG
    enabled: bool = True
    description: str = ""


class GuardrailRules(BaseModel):
    """Complete guardrail rule set."""

    version: str = "1.0"
    rules: list[GuardrailRule] = []


class GuardrailValidateResponse(BaseModel):
    """Result of validating a rule set."""

    valid: bool
    errors: list[str] = []
    rule_count: int = 0


class GuardrailTestRequest(BaseModel):
    """Request to test a message against guardrails."""

    message: str
    context: dict[str, Any] = {}


class GuardrailTestResponse(BaseModel):
    """Result of testing a message."""

    blocked: bool = False
    triggered_rules: list[str] = []
    action: str = "PASS"


class GuardrailMetrics(BaseModel):
    """Guardrail trigger metrics."""

    total_requests: int = 0
    blocked_count: int = 0
    warned_count: int = 0
    top_triggered_rules: list[str] = []


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get(
    "", response_model=GuardrailRules, status_code=status.HTTP_200_OK, summary="Get guardrail rules"
)
async def get_guardrails(
    admin_user_id: AdminUserDep, storage_client: StorageClientDep
) -> GuardrailRules:
    data = await _load_json(_RULES_PATH, storage_client)
    return GuardrailRules(**data) if data else GuardrailRules()


@router.put(
    "",
    response_model=GuardrailRules,
    status_code=status.HTTP_200_OK,
    summary="Replace guardrail rules",
)
async def put_guardrails(
    body: GuardrailRules, admin_user_id: AdminUserDep, storage_client: StorageClientDep
) -> GuardrailRules:
    await _save_json(_RULES_PATH, storage_client, body.model_dump())
    return body


@router.post(
    "/validate",
    response_model=GuardrailValidateResponse,
    status_code=status.HTTP_200_OK,
    summary="Validate guardrail rules",
    description="Validate a rule set without persisting it.",
)
async def validate_guardrails(
    body: GuardrailRules,
    admin_user_id: AdminUserDep,
) -> GuardrailValidateResponse:
    """Basic validation: check for duplicate rule IDs and empty patterns."""
    errors: list[str] = []
    seen_ids: set[str] = set()
    for rule in body.rules:
        if rule.rule_id in seen_ids:
            errors.append(f"Duplicate rule_id: {rule.rule_id!r}")
        seen_ids.add(rule.rule_id)
        if not rule.pattern.strip():
            errors.append(f"Empty pattern in rule {rule.rule_id!r}")
    return GuardrailValidateResponse(
        valid=len(errors) == 0,
        errors=errors,
        rule_count=len(body.rules),
    )


@router.post(
    "/test",
    response_model=GuardrailTestResponse,
    status_code=status.HTTP_200_OK,
    summary="Test message against guardrails",
    description="Check whether a message would be blocked by the current rules.",
)
async def test_guardrails(
    body: GuardrailTestRequest,
    admin_user_id: AdminUserDep,
    storage_client: StorageClientDep,
) -> GuardrailTestResponse:
    """Test a message against the current guardrail rules."""
    import re

    data = await _load_json(_RULES_PATH, storage_client)
    rules_data = GuardrailRules(**data) if data else GuardrailRules()

    triggered: list[str] = []
    for rule in rules_data.rules:
        if not rule.enabled:
            continue
        try:
            if re.search(rule.pattern, body.message, re.IGNORECASE):
                triggered.append(rule.rule_id)
        except re.error:
            pass

    blocked = any(r.action == "BLOCK" for r in rules_data.rules if r.rule_id in triggered)
    return GuardrailTestResponse(
        blocked=blocked,
        triggered_rules=triggered,
        action="BLOCK" if blocked else "PASS",
    )


@router.get(
    "/metrics",
    response_model=GuardrailMetrics,
    status_code=status.HTTP_200_OK,
    summary="Get guardrail metrics",
)
async def get_guardrail_metrics(
    admin_user_id: AdminUserDep, storage_client: StorageClientDep
) -> GuardrailMetrics:
    data = await _load_json(_METRICS_PATH, storage_client)
    return GuardrailMetrics(**data) if data else GuardrailMetrics()
