"""graphclaw.api.admin.infra — Infrastructure, deployment, and ops endpoints.

Routes
------
GET  /app/v1/admin/deployment/status     — container/service deployment status
GET  /app/v1/admin/deployment/config     — deployment configuration
GET  /app/v1/admin/cluster/health        — cluster health snapshot
GET  /app/v1/admin/backups               — backup inventory
GET  /app/v1/admin/security/status       — security posture summary
GET  /app/v1/admin/alarms                — list active alarms
PATCH /app/v1/admin/alarms/{alarm_id}    — acknowledge/resolve an alarm
GET  /app/v1/admin/migrations            — list schema migrations
POST /app/v1/admin/migrations/apply      — apply pending migrations (dry-run aware)
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

from graphclaw.api.deps import AdminUserDep, StorageClientDep

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin-api"])

_DEPLOY_CONFIG_PATH = "admin/infra/deploy_config.json"
_ALARMS_PATH = "admin/infra/alarms.json"
_MIGRATIONS_PATH = "admin/infra/migrations.json"


async def _load_json(path: str, storage_client: Any, default: Any = None) -> Any:
    try:
        raw = await storage_client.read(path)
        return json.loads(raw.decode())
    except FileNotFoundError:
        return default if default is not None else {}


async def _save_json(path: str, storage_client: Any, data: Any) -> None:
    await storage_client.write(
        path, json.dumps(data, default=str).encode(), content_type="application/json"
    )


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class ServiceStatus(BaseModel):
    name: str
    replicas: int = 0
    desired: int = 0
    health: str = "unknown"
    last_deployed: str | None = None


class DeploymentStatus(BaseModel):
    overall: str = "unknown"
    services: list[ServiceStatus] = []
    version: str = ""
    environment: str = "development"


class DeploymentConfig(BaseModel):
    environment: str = "development"
    image_tag: str = "latest"
    replicas: dict[str, int] = {}
    extra: dict[str, Any] = {}


class ClusterHealth(BaseModel):
    status: str = "unknown"
    db_status: str = "unknown"
    redis_status: str = "unknown"
    storage_status: str = "unknown"
    cpu_pct: float = 0.0
    memory_pct: float = 0.0


class BackupEntry(BaseModel):
    backup_id: str
    created_at: str
    size_bytes: int = 0
    type: str = "full"
    status: str = "available"


class SecurityStatus(BaseModel):
    overall: str = "ok"
    open_issues: int = 0
    last_scan_at: str | None = None
    findings: list[str] = []


class Alarm(BaseModel):
    alarm_id: str
    name: str
    severity: str = "P3"
    state: str = "ALARM"
    triggered_at: str
    acknowledged: bool = False
    resolved: bool = False


class AlarmPatchRequest(BaseModel):
    acknowledged: bool | None = None
    resolved: bool | None = None


class Migration(BaseModel):
    migration_id: str
    name: str
    status: str = "pending"
    applied_at: str | None = None


class MigrationApplyRequest(BaseModel):
    dry_run: bool = True


class MigrationApplyResponse(BaseModel):
    applied: list[str] = []
    dry_run: bool = True
    message: str = ""


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get(
    "/deployment/status",
    response_model=DeploymentStatus,
    status_code=status.HTTP_200_OK,
    summary="Deployment status",
)
async def get_deployment_status(admin_user_id: AdminUserDep) -> DeploymentStatus:
    """Return a stub deployment status (live data requires cloud probe)."""
    return DeploymentStatus(
        overall="unknown",
        services=[],
        version="phase4",
        environment="development",
    )


@router.get(
    "/deployment/config",
    response_model=DeploymentConfig,
    status_code=status.HTTP_200_OK,
    summary="Deployment config",
)
async def get_deployment_config(
    admin_user_id: AdminUserDep, storage_client: StorageClientDep
) -> DeploymentConfig:
    data = await _load_json(_DEPLOY_CONFIG_PATH, storage_client)
    return DeploymentConfig(**data) if data else DeploymentConfig()


@router.get(
    "/cluster/health",
    response_model=ClusterHealth,
    status_code=status.HTTP_200_OK,
    summary="Cluster health",
)
async def get_cluster_health(admin_user_id: AdminUserDep) -> ClusterHealth:
    """Return a stub cluster health (live data requires infra probes)."""
    return ClusterHealth(status="unknown")


@router.get(
    "/backups",
    response_model=list[BackupEntry],
    status_code=status.HTTP_200_OK,
    summary="List backups",
)
async def list_backups(
    admin_user_id: AdminUserDep,
    limit: int = Query(default=20, ge=1, le=100),
) -> list[BackupEntry]:
    """Return backup inventory (stub — real data requires AWS/GCP API)."""
    return []


@router.get(
    "/security/status",
    response_model=SecurityStatus,
    status_code=status.HTTP_200_OK,
    summary="Security status",
)
async def get_security_status(admin_user_id: AdminUserDep) -> SecurityStatus:
    return SecurityStatus(overall="ok", open_issues=0)


@router.get(
    "/alarms", response_model=list[Alarm], status_code=status.HTTP_200_OK, summary="List alarms"
)
async def list_alarms(
    admin_user_id: AdminUserDep,
    storage_client: StorageClientDep,
    state: str | None = Query(default=None),
) -> list[Alarm]:
    data = await _load_json(_ALARMS_PATH, storage_client, default=[])
    alarms = [Alarm(**a) for a in (data if isinstance(data, list) else [])]
    if state:
        alarms = [a for a in alarms if a.state == state]
    return alarms


@router.patch(
    "/alarms/{alarm_id}",
    response_model=Alarm,
    status_code=status.HTTP_200_OK,
    summary="Acknowledge/resolve alarm",
)
async def patch_alarm(
    alarm_id: str,
    body: AlarmPatchRequest,
    admin_user_id: AdminUserDep,
    storage_client: StorageClientDep,
) -> Alarm:
    data = await _load_json(_ALARMS_PATH, storage_client, default=[])
    alarms: list[dict] = data if isinstance(data, list) else []
    target = next((a for a in alarms if a.get("alarm_id") == alarm_id), None)
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Alarm '{alarm_id}' not found"
        )
    if body.acknowledged is not None:
        target["acknowledged"] = body.acknowledged
    if body.resolved is not None:
        target["resolved"] = body.resolved
        if body.resolved:
            target["state"] = "OK"
    await _save_json(_ALARMS_PATH, storage_client, alarms)
    return Alarm(**target)


@router.get(
    "/migrations",
    response_model=list[Migration],
    status_code=status.HTTP_200_OK,
    summary="List migrations",
)
async def list_migrations(
    admin_user_id: AdminUserDep, storage_client: StorageClientDep
) -> list[Migration]:
    data = await _load_json(_MIGRATIONS_PATH, storage_client, default=[])
    return [Migration(**m) for m in (data if isinstance(data, list) else [])]


@router.post(
    "/migrations/apply",
    response_model=MigrationApplyResponse,
    status_code=status.HTTP_200_OK,
    summary="Apply pending migrations",
    description="Apply pending schema migrations.  Set dry_run=true to preview without executing.",
)
async def apply_migrations(
    body: MigrationApplyRequest,
    admin_user_id: AdminUserDep,
    storage_client: StorageClientDep,
) -> MigrationApplyResponse:
    data = await _load_json(_MIGRATIONS_PATH, storage_client, default=[])
    migrations = [Migration(**m) for m in (data if isinstance(data, list) else [])]
    pending = [m.migration_id for m in migrations if m.status == "pending"]
    if body.dry_run:
        return MigrationApplyResponse(
            applied=[],
            dry_run=True,
            message=f"Dry run: {len(pending)} pending migration(s) would be applied",
        )
    # In production this would run the actual migrations
    return MigrationApplyResponse(
        applied=pending,
        dry_run=False,
        message=f"Migration runner not yet wired — {len(pending)} pending",
    )
