"""graphclaw.api.admin.judge — LLM-as-a-Judge configuration and results endpoints.

Routes
------
GET  /app/v1/admin/llm-judge/config   — get judge configuration
PUT  /app/v1/admin/llm-judge/config   — update judge configuration
GET  /app/v1/admin/llm-judge/results  — list recent judge evaluation results
GET  /app/v1/admin/llm-judge/stats    — aggregated judge evaluation statistics
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Query, status
from pydantic import BaseModel

from graphclaw.api.deps import AdminUserDep, StorageClientDep

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/llm-judge", tags=["admin-api"])

_JUDGE_CONFIG_PATH = "admin/judge/config.json"
_JUDGE_RESULTS_PATH = "admin/judge/results.json"


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


class JudgeConfig(BaseModel):
    """LLM-as-a-Judge configuration."""

    enabled: bool = False
    judge_model: str = "claude-opus-4-6"
    sample_rate: float = 0.1
    criteria: list[str] = ["helpfulness", "accuracy", "safety"]
    auto_flag_threshold: float = 0.6
    extra: dict[str, Any] = {}


class JudgeResult(BaseModel):
    """A single LLM evaluation result."""

    result_id: str
    session_id: str
    evaluated_at: str
    scores: dict[str, float] = {}
    overall_score: float = 0.0
    flagged: bool = False
    notes: str = ""


class JudgeStats(BaseModel):
    """Aggregated judge statistics over a time window."""

    total_evaluations: int = 0
    flagged_count: int = 0
    avg_overall_score: float = 0.0
    criteria_averages: dict[str, float] = {}
    window_days: int = 7


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/config", response_model=JudgeConfig, status_code=status.HTTP_200_OK, summary="Get judge config")
async def get_judge_config(admin_user_id: AdminUserDep, storage_client: StorageClientDep) -> JudgeConfig:
    data = await _load_json(_JUDGE_CONFIG_PATH, storage_client)
    return JudgeConfig(**data) if data else JudgeConfig()


@router.put("/config", response_model=JudgeConfig, status_code=status.HTTP_200_OK, summary="Update judge config")
async def put_judge_config(body: JudgeConfig, admin_user_id: AdminUserDep, storage_client: StorageClientDep) -> JudgeConfig:
    await _save_json(_JUDGE_CONFIG_PATH, storage_client, body.model_dump())
    return body


@router.get(
    "/results",
    response_model=list[JudgeResult],
    status_code=status.HTTP_200_OK,
    summary="List judge results",
    description="Return recent LLM evaluation results (most recent first).",
)
async def list_judge_results(
    admin_user_id: AdminUserDep,
    storage_client: StorageClientDep,
    limit: int = Query(default=50, ge=1, le=200),
) -> list[JudgeResult]:
    data = await _load_json(_JUDGE_RESULTS_PATH, storage_client, default=[])
    results = [JudgeResult(**r) for r in (data if isinstance(data, list) else [])]
    return results[:limit]


@router.get(
    "/stats",
    response_model=JudgeStats,
    status_code=status.HTTP_200_OK,
    summary="Get judge statistics",
    description="Return aggregated evaluation statistics.",
)
async def get_judge_stats(
    admin_user_id: AdminUserDep,
    storage_client: StorageClientDep,
    window_days: int = Query(default=7, ge=1, le=90),
) -> JudgeStats:
    data = await _load_json(_JUDGE_RESULTS_PATH, storage_client, default=[])
    results: list[dict] = data if isinstance(data, list) else []
    total = len(results)
    if total == 0:
        return JudgeStats(window_days=window_days)
    flagged = sum(1 for r in results if r.get("flagged", False))
    avg = sum(r.get("overall_score", 0.0) for r in results) / total
    return JudgeStats(
        total_evaluations=total,
        flagged_count=flagged,
        avg_overall_score=round(avg, 4),
        window_days=window_days,
    )
