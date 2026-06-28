# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.gateway.app — FastAPI application factory for the channel gateway.

Description
-----------
Provides ``create_app``, which constructs and returns a fully configured
FastAPI application instance.  The application manages channel adapters via
``ChannelRegistry`` and the broker lifecycle via FastAPI's ``lifespan`` context
manager.

Endpoints:
- ``GET  /health``           — Liveness probe (always returns 200).
- ``GET  /health/ready``     — Readiness probe (checks broker connectivity).
- ``POST /api/v1/inbound``   — Accept an ``InboundMessage`` and publish to the
                               ``INBOUND_MESSAGES`` broker queue.
- ``POST /api/v1/trigger``   — On-demand trigger for ad-hoc agent activations.

Design Patterns
---------------
- Factory: ``create_app`` is the single entry-point for constructing the ASGI
  app; this allows different broker/registry configurations to be injected in
  tests without modifying module-level state.
- Lifespan Context Manager: Startup and shutdown logic lives in a single
  ``asynccontextmanager`` function, avoiding deprecated ``on_event`` hooks.
- Dependency Injection via ``app.state``: The broker and registry are stored on
  ``app.state`` so that endpoint handlers can access them without module-level
  globals.

Public API
----------
- create_app: Construct and return a configured ``FastAPI`` instance.

Dependencies
------------
- graphclaw.gateway.schemas: InboundMessage.
- graphclaw.gateway.channel_registry: build_registry.
- graphclaw.infra.broker: MessageBroker, INBOUND_MESSAGES.
- fastapi: FastAPI, Request (third-party).
- contextlib: asynccontextmanager (stdlib).
- logging: structured logging.
- os: environment variable access (stdlib).

Notes
-----
If ``broker`` is ``None`` (the default), the application still starts and
serves traffic, but ``/health/ready`` returns ``status: "degraded"`` and
publishing to the broker is skipped.  This enables lightweight integration
testing without a running message broker.

Channel adapters are discovered and started via ``build_registry``.  Each
adapter reads its own environment variables for credentials.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from graphclaw.gateway.channel_registry import build_registry
from graphclaw.gateway.channels.email.ses_receiver import SESEmailReceiver
from graphclaw.gateway.deps import init_services, shutdown_services
from graphclaw.gateway.rate_limiter import RateLimitMiddleware
from graphclaw.gateway.schemas import InboundMessage
from graphclaw.infra.broker import INBOUND_MESSAGES, MessageBroker

logger = logging.getLogger(__name__)

_CRITICAL_DEPENDENCIES = ("broker", "database", "storage")
_DEFAULT_LLM_PROVIDER = "anthropic"
_DEFAULT_LLM_PROVIDER_ENV = "GRAPHCLAW_DEFAULT_LLM_PROVIDER"
_LLM_PROVIDER_SECRET_CANDIDATES: dict[str, tuple[str, ...]] = {
    "anthropic": ("ANTHROPIC_API_KEY", "graphclaw/org/llm/anthropic"),
    "openai": ("OPENAI_API_KEY", "graphclaw/org/llm/openai"),
}


def _build_dependency_status(ok: bool, reason: str = "", critical: bool = True) -> dict[str, Any]:
    return {
        "ok": ok,
        "critical": critical,
        "reason": reason,
    }


def _compute_readiness(app: FastAPI) -> tuple[bool, dict[str, dict[str, Any]]]:
    """Compute readiness state from startup diagnostics or fallback runtime state."""
    startup_health = getattr(app.state, "startup_health", None)
    if isinstance(startup_health, dict) and startup_health:
        dependencies = startup_health
    else:
        broker_ok = getattr(app.state, "broker", None) is not None
        dependencies = {
            "broker": _build_dependency_status(
                ok=broker_ok,
                reason="" if broker_ok else "broker not configured",
            )
        }

    is_ready = True
    for dep_name, dep in dependencies.items():
        if dep_name in _CRITICAL_DEPENDENCIES and bool(dep.get("critical", True)):
            if not bool(dep.get("ok", False)):
                is_ready = False
                break
    return is_ready, dependencies


def _normalize_default_provider(raw: str | None) -> str:
    """Normalize and validate the default LLM provider env value."""
    candidate = (raw or _DEFAULT_LLM_PROVIDER).strip().lower()
    if candidate in _LLM_PROVIDER_SECRET_CANDIDATES:
        return candidate
    logger.warning(
        "GraphClaw: invalid %s=%s; defaulting to %s",
        _DEFAULT_LLM_PROVIDER_ENV,
        raw,
        _DEFAULT_LLM_PROVIDER,
    )
    return _DEFAULT_LLM_PROVIDER


async def _read_first_available_secret(secrets_client: Any, keys: tuple[str, ...]) -> str | None:
    """Return the first non-empty secret value available from candidate keys."""
    for key in keys:
        try:
            value = await secrets_client.get_secret(key)
        except KeyError:
            continue
        except Exception as exc:  # noqa: BLE001
            logger.warning("GraphClaw: failed reading secret '%s': %s", key, exc)
            continue

        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


async def _select_startup_llm_provider_and_key(
    secrets_client: Any,
) -> tuple[str | None, str | None]:
    """Select startup LLM provider and API key using key-availability policy.

    Policy:
    - if only one provider key exists, select that provider
    - if both exist, select by GRAPHCLAW_DEFAULT_LLM_PROVIDER
    - if none exist, return (None, None)
    """
    anthropic_key = await _read_first_available_secret(
        secrets_client,
        _LLM_PROVIDER_SECRET_CANDIDATES["anthropic"],
    )
    openai_key = await _read_first_available_secret(
        secrets_client,
        _LLM_PROVIDER_SECRET_CANDIDATES["openai"],
    )

    if anthropic_key and openai_key:
        default_provider = _normalize_default_provider(os.getenv(_DEFAULT_LLM_PROVIDER_ENV))
        if default_provider == "openai":
            return "openai", openai_key
        return "anthropic", anthropic_key

    if anthropic_key:
        return "anthropic", anthropic_key
    if openai_key:
        return "openai", openai_key

    return None, None


def create_app(broker: MessageBroker | None = None) -> FastAPI:
    """Construct and return a fully configured FastAPI gateway application.

    Parameters
    ----------
    broker:
        ``MessageBroker`` instance to use for publishing and consuming
        messages.  When ``None``, the application operates in a degraded
        mode suitable for health-check-only deployments and unit tests.

    Returns
    -------
    FastAPI:
        A configured ASGI application ready to be served by an ASGI server
        such as ``uvicorn``.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI):  # type: ignore[type-arg]
        # ── Startup ──────────────────────────────────────────────────────
        app.state.broker = broker
        startup_mode = os.environ.get("GRAPHCLAW_STARTUP_MODE", "degraded").strip().lower()
        if startup_mode not in {"degraded", "strict"}:
            logger.warning(
                "GraphClaw: invalid GRAPHCLAW_STARTUP_MODE=%s; defaulting to degraded",
                startup_mode,
            )
            startup_mode = "degraded"
        app.state.startup_mode = startup_mode
        app.state.startup_health = {
            "broker": _build_dependency_status(
                ok=broker is not None,
                reason="" if broker is not None else "broker not configured",
            ),
            "database": _build_dependency_status(ok=False, reason="database not initialised"),
            "storage": _build_dependency_status(ok=False, reason="storage not initialised"),
        }

        # Initialise the deps module singletons so sub-router Depends work
        await init_services()

        registry = build_registry()
        app.state.registry = registry

        if broker is not None:
            await registry.start_all(broker)

        # ── Initialise API-layer services on app.state ────────────────────
        _db_pool = None
        _sub_agent_pool = None
        _agent_health_monitor = None
        _agent_event_consumer = None
        _trigger_engine = None
        try:
            from graphclaw.db.age.connection import create_pgbouncer_pool
            from graphclaw.db.factory import create_graph_store, create_query_engine
            from graphclaw.infra.secrets import AWSSecretsClient, EnvFileSecretsClient
            from graphclaw.infra.storage import S3StorageClient
            from graphclaw.scoring.engine import ScoringEngine

            # Database — only connect when DATABASE_URL is present
            database_url = os.environ.get("DATABASE_URL", "")
            if database_url:
                _db_pool = await create_pgbouncer_pool()
                app.state.pool = _db_pool  # exposed for NotificationService
                app.state.graph_store = create_graph_store("age", pool=_db_pool)
                app.state.query_engine = create_query_engine("age", pool=_db_pool)
                app.state.startup_health["database"] = _build_dependency_status(ok=True)
                logger.info("GraphClaw: graph store and query engine initialised")

                # Wave 0: Run no-delete startup probe if enforcement is enabled.
                no_delete_enforcement = (
                    os.environ.get("GRAPHCLAW_NO_DELETE_ENFORCEMENT", "false").lower() == "true"
                )
                if no_delete_enforcement:
                    from graphclaw.auth.principals import startup_assert_no_delete  # noqa: PLC0415

                    await startup_assert_no_delete(_db_pool)  # SystemExit on failure
            else:
                app.state.startup_health["database"] = _build_dependency_status(
                    ok=False,
                    reason="DATABASE_URL not set",
                )
                logger.warning("GraphClaw: DATABASE_URL not set — graph store unavailable")

            # Storage (MinIO in dev, S3 in production)
            app.state.storage_client = None
            try:
                app.state.storage_client = S3StorageClient(
                    bucket=os.environ.get("STORAGE_BUCKET", "graphclaw"),
                    endpoint_url=os.environ.get("STORAGE_ENDPOINT_URL") or None,
                    region=os.environ.get("STORAGE_REGION", "us-east-1"),
                )
                app.state.startup_health["storage"] = _build_dependency_status(ok=True)
                logger.info("GraphClaw: storage client initialised")
            except Exception as exc:  # noqa: BLE001
                app.state.startup_health["storage"] = _build_dependency_status(
                    ok=False,
                    reason=str(exc),
                )
                logger.error("GraphClaw: storage client initialisation failed — %s", exc)

            if startup_mode == "strict":
                failed_critical = [
                    dep
                    for dep in _CRITICAL_DEPENDENCIES
                    if not app.state.startup_health.get(dep, {}).get("ok", False)
                ]
                if failed_critical:
                    reasons = ", ".join(
                        f"{dep}: {app.state.startup_health[dep].get('reason', 'unhealthy')}"
                        for dep in failed_critical
                    )
                    raise RuntimeError(
                        f"GraphClaw strict startup failed due to critical dependencies: {reasons}"
                    )

            # Seed system content (idempotent — skips existing objects)
            if app.state.storage_client is not None:
                try:
                    from graphclaw.gateway.seeding import seed_system_content  # noqa: PLC0415

                    await seed_system_content(app.state.storage_client)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("GraphClaw: system content seeding failed — %s", exc)

                # Wave 0 (FR-DEL-008): audit storage lifecycle rules at startup.
                try:
                    from graphclaw.observability.startup_audit import (  # noqa: PLC0415
                        startup_assert_no_lifecycle_rules,
                    )

                    await startup_assert_no_lifecycle_rules(app.state.storage_client)
                except SystemExit:
                    raise  # propagate fatal lifecycle violation
                except Exception as exc:  # noqa: BLE001
                    logger.warning("GraphClaw: lifecycle audit failed (non-fatal) — %s", exc)

            # Secrets backend
            secrets_backend = os.environ.get("SECRETS_BACKEND", "env_file")
            if secrets_backend == "aws_sm":
                app.state.secrets_client = AWSSecretsClient(
                    region=os.environ.get("AWS_REGION", "us-east-1")
                )
            else:
                app.state.secrets_client = EnvFileSecretsClient()
            logger.info("GraphClaw: secrets client initialised (%s)", secrets_backend)

            # Scoring engine — stateless, no external deps
            app.state.scoring_engine = ScoringEngine()
            logger.info("GraphClaw: scoring engine initialised")

            # FR-IN-003: AgentChannelIdentity registry (in-memory, hot-reloaded by admin API)
            from graphclaw.gateway.agent_channel_identity import (  # noqa: PLC0415
                AgentChannelIdentityRegistry,
            )

            app.state.channel_registry = AgentChannelIdentityRegistry()
            logger.info("GraphClaw: AgentChannelIdentityRegistry initialised (empty)")

            # Redis — required for auth OTC exchange and user event publishing
            app.state.redis = None
            redis_url = os.environ.get("REDIS_URL", "")
            if redis_url:
                try:
                    import redis.asyncio as aioredis  # noqa: PLC0415

                    _redis_client = aioredis.from_url(redis_url, decode_responses=True)
                    await _redis_client.ping()
                    app.state.redis = _redis_client
                    logger.info("GraphClaw: Redis client initialised (%s)", redis_url)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("GraphClaw: Redis connection failed — %s", exc)
            else:
                logger.warning("GraphClaw: REDIS_URL not set — auth OTC exchange unavailable")

            # AgentLoop + LLM client + AgentEventConsumer
            if database_url:
                try:
                    from graphclaw.agent.event_consumer import AgentEventConsumer  # noqa: PLC0415
                    from graphclaw.agent.main_orchestrator import MainOrchestrator  # noqa: PLC0415
                    from graphclaw.agent.outbound import OutboundDispatcher  # noqa: PLC0415
                    from graphclaw.llm.factory import create_llm_client  # noqa: PLC0415
                    from graphclaw.state.machine import StateMachine  # noqa: PLC0415

                    llm_client = None
                    selected_provider, selected_key = await _select_startup_llm_provider_and_key(
                        app.state.secrets_client
                    )
                    if selected_provider and selected_key:
                        try:
                            llm_client = create_llm_client(
                                selected_provider,
                                api_key=selected_key,
                            )
                            logger.info(
                                "GraphClaw: llm client initialised (provider=%s)",
                                selected_provider,
                            )
                        except Exception as exc:  # noqa: BLE001
                            logger.error(
                                "GraphClaw: llm client initialisation failed for provider=%s: %s",
                                selected_provider,
                                exc,
                            )
                    else:
                        logger.warning(
                            "GraphClaw: no LLM API key configured (checked Anthropic/OpenAI). "
                            "Chat will be available but report LLM not configured."
                        )

                    agent_id = os.environ.get("AGENT_ID", "main")

                    # Optional: skill registry, worker pool, MCP registry
                    _skill_registry = None
                    _worker_pool = None
                    _mcp_registry = None
                    if app.state.storage_client is not None:
                        try:
                            from graphclaw.skills.registry import (
                                SkillRegistryService,  # noqa: PLC0415
                            )

                            _skill_registry = SkillRegistryService(
                                storage_client=app.state.storage_client,
                            )
                        except Exception:
                            pass
                        try:
                            from graphclaw.mcp.registry import MCPRegistry  # noqa: PLC0415

                            _mcp_registry = MCPRegistry(
                                storage_client=app.state.storage_client,
                                secrets_client=app.state.secrets_client,
                            )
                        except Exception:
                            pass
                    if llm_client is not None:
                        try:
                            from graphclaw.skills.llm_router import LLMRouter  # noqa: PLC0415
                            from graphclaw.skills.worker import WorkerPool  # noqa: PLC0415

                            _llm_router = LLMRouter(llm_client=llm_client)
                            _worker_pool = WorkerPool(pool_size=4, llm_router=_llm_router)
                        except Exception:
                            pass
                    else:
                        logger.warning(
                            "GraphClaw: skipping skill worker pool initialisation because LLM "
                            "is not configured"
                        )

                    # Phase 5: Sub-agent pool + health monitor
                    _sub_agent_pool = None
                    _agent_health_monitor = None
                    _dispatch_planner = None
                    _result_collector = None
                    if broker is not None and llm_client is not None:
                        try:
                            from graphclaw.agent.dispatch_planner import (
                                AgentDispatchPlanner,  # noqa: PLC0415
                            )
                            from graphclaw.agent.health_monitor import (
                                AgentHealthMonitor,  # noqa: PLC0415
                            )
                            from graphclaw.agent.result_collector import (
                                ResultCollector,  # noqa: PLC0415
                            )
                            from graphclaw.agent.sub_agent_pool import SubAgentPool  # noqa: PLC0415
                            from graphclaw.infra.config import AgentPoolConfig  # noqa: PLC0415
                            from graphclaw.skills.llm_router import LLMRouter  # noqa: PLC0415
                            from graphclaw.skills.worker import WorkerPool  # noqa: PLC0415
                            from graphclaw.state.machine import StateMachine as _SM  # noqa: PLC0415

                            pool_cfg = AgentPoolConfig.from_env()

                            # Dedicated LLM router + worker pool for sub-agents (isolated from orchestrator)
                            _subagent_llm_router = LLMRouter(llm_client=llm_client)
                            _subagent_worker_pool = WorkerPool(
                                pool_size=pool_cfg.subagent_worker_pool_size,
                                llm_router=_subagent_llm_router,
                            )

                            _sub_agent_pool = SubAgentPool(
                                max_size=pool_cfg.max_concurrent_agents,
                                broker=broker,
                                llm_client=llm_client,
                                storage=app.state.storage_client,
                                worker_pool=_subagent_worker_pool,
                                skill_registry=_skill_registry,
                                mcp_registry=_mcp_registry,
                                heartbeat_interval=pool_cfg.heartbeat_interval_seconds,
                                execution_timeout_seconds=(
                                    pool_cfg.subagent_execution_timeout_seconds
                                ),
                                tool_timeout_seconds=pool_cfg.subagent_tool_timeout_seconds,
                                tool_max_retries=pool_cfg.subagent_tool_max_retries,
                                retry_backoff_base_ms=pool_cfg.subagent_retry_backoff_base_ms,
                                retry_backoff_max_ms=pool_cfg.subagent_retry_backoff_max_ms,
                                retryable_skills=set(pool_cfg.subagent_retryable_skills),
                                retryable_mcp_tools=set(pool_cfg.subagent_retryable_mcp_tools),
                            )

                            _agent_health_monitor = AgentHealthMonitor(
                                broker=broker,
                                state_machine=_SM(),
                                check_interval=30,
                                heartbeat_timeout=pool_cfg.heartbeat_timeout_seconds,
                            )

                            if hasattr(app.state, "graph_store"):
                                _dispatch_planner = AgentDispatchPlanner(
                                    query_engine=app.state.query_engine,
                                )
                                _result_collector = ResultCollector(
                                    graph_repo=app.state.graph_store,
                                    worker_pool=_worker_pool or _subagent_worker_pool,
                                    storage_client=app.state.storage_client,
                                    user_id=os.environ.get("GRAPHCLAW_USER_ID", ""),
                                    agent_id=agent_id,
                                )

                            await _subagent_worker_pool.start()
                            await _sub_agent_pool.start()
                            await _agent_health_monitor.start()
                            app.state.sub_agent_pool = _sub_agent_pool
                            app.state.agent_health_monitor = _agent_health_monitor
                            logger.info(
                                "GraphClaw: sub-agent pool started (max=%d, subagent_workers=%d)",
                                pool_cfg.max_concurrent_agents,
                                pool_cfg.subagent_worker_pool_size,
                            )
                        except Exception as exc:  # noqa: BLE001
                            logger.error(
                                "GraphClaw: sub-agent pool initialisation failed — %s",
                                exc,
                                exc_info=exc,
                            )
                    elif broker is not None:
                        logger.warning(
                            "GraphClaw: skipping sub-agent pool initialisation because LLM is "
                            "not configured"
                        )

                    # User event publisher — Redis if available, else no-op
                    _event_publisher = None
                    try:
                        from graphclaw.infra.user_events import (  # noqa: PLC0415
                            NullUserEventPublisher,
                            RedisUserEventPublisher,
                        )

                        _redis = getattr(app.state, "redis", None)
                        if _redis is not None:
                            _event_publisher = RedisUserEventPublisher(_redis)
                            logger.info("GraphClaw: user event publisher (Redis) initialised")
                        else:
                            _event_publisher = NullUserEventPublisher()
                            logger.info("GraphClaw: user event publisher (Null) initialised")
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("GraphClaw: user event publisher init failed — %s", exc)

                    agent_loop = MainOrchestrator(
                        graph_repo=app.state.graph_store,
                        scoring_engine=app.state.scoring_engine,
                        state_machine=StateMachine(),
                        llm_client=llm_client,
                        storage_client=app.state.storage_client,
                        agent_id=agent_id,
                        skill_registry=_skill_registry,
                        worker_pool=_worker_pool,
                        mcp_registry=_mcp_registry,
                        broker=broker,
                        dispatch_planner=_dispatch_planner,
                        sub_agent_pool=_sub_agent_pool,
                        event_publisher=_event_publisher,
                        redis_client=getattr(app.state, "redis", None),
                        db_pool=getattr(app.state, "pool", None),
                    )
                    app.state.agent_loop = agent_loop
                    logger.info("GraphClaw: agent loop initialised (agent_id=%s)", agent_id)

                    if broker is not None:
                        dispatcher = OutboundDispatcher.from_env(broker=broker)
                        default_user_id = os.environ.get("GRAPHCLAW_USER_ID", "")
                        _agent_event_consumer = AgentEventConsumer(
                            broker=broker,
                            agent_loop=agent_loop,
                            dispatcher=dispatcher,
                            default_user_id=default_user_id,
                            storage=app.state.storage_client,
                            health_monitor=_agent_health_monitor,
                            result_collector=_result_collector,
                        )
                        await _agent_event_consumer.start()
                        app.state.agent_event_consumer = _agent_event_consumer
                        logger.info("GraphClaw: agent event consumer started")

                        # TriggerEngine: schedule loop + inbound->trigger conversion loop
                        from graphclaw.triggers.engine import TriggerEngine  # noqa: PLC0415
                        from graphclaw.triggers.models import (  # noqa: PLC0415
                            TriggerConfig,
                            TriggerType,
                        )
                        from graphclaw.triggers.persistence import (  # noqa: PLC0415
                            load_trigger_schedule,
                            save_trigger_schedule,
                        )
                        from graphclaw.triggers.scheduler import TriggerScheduler  # noqa: PLC0415

                        scheduler = TriggerScheduler()
                        loaded_trigger_count = 0
                        trigger_user_id = os.environ.get("GRAPHCLAW_USER_ID", "")

                        if trigger_user_id:
                            from graphclaw.cross_tenant.acl import (
                                CallerContext as _CallerContext,  # noqa: PLC0415
                            )

                            _trigger_ctx = _CallerContext(
                                user_id=trigger_user_id,
                                org_id="default",
                                principal="admin_principal",
                            )
                            if app.state.graph_store is not None:
                                persisted = await load_trigger_schedule(
                                    app.state.graph_store,
                                    trigger_user_id,
                                    agent_id=agent_id,
                                    caller_context=_trigger_ctx,
                                )
                                for cfg in persisted:
                                    scheduler.register(cfg)
                                    loaded_trigger_count += 1

                            trigger_path = f"{trigger_user_id}/agents/{agent_id}/triggers.json"
                            imported_from_storage = False

                            if loaded_trigger_count == 0 and app.state.storage_client is not None:
                                try:
                                    raw_bytes = await app.state.storage_client.read(trigger_path)
                                    parsed = json.loads(raw_bytes.decode(errors="replace"))
                                    if isinstance(parsed, list):
                                        for idx, item in enumerate(parsed):
                                            try:
                                                cfg = TriggerConfig.model_validate(item)
                                                scheduler.register(cfg)
                                                loaded_trigger_count += 1
                                            except Exception as exc:  # noqa: BLE001
                                                logger.warning(
                                                    "GraphClaw: invalid trigger config at index %d in %s: %s",
                                                    idx,
                                                    trigger_path,
                                                    exc,
                                                )
                                        imported_from_storage = loaded_trigger_count > 0
                                    else:
                                        logger.warning(
                                            "GraphClaw: trigger config file %s is not a JSON list",
                                            trigger_path,
                                        )
                                except FileNotFoundError:
                                    logger.info(
                                        "GraphClaw: no persisted trigger config found at %s",
                                        trigger_path,
                                    )
                                except Exception as exc:  # noqa: BLE001
                                    logger.warning(
                                        "GraphClaw: failed to load persisted trigger config from %s: %s",
                                        trigger_path,
                                        exc,
                                    )

                            if imported_from_storage and app.state.graph_store is not None:
                                try:
                                    await save_trigger_schedule(
                                        app.state.graph_store,
                                        trigger_user_id,
                                        list(getattr(scheduler, "_triggers", {}).values()),
                                        caller_context=_trigger_ctx,
                                    )
                                    logger.info(
                                        "GraphClaw: imported trigger schedule into DB for user=%s",
                                        trigger_user_id,
                                    )
                                except Exception as exc:  # noqa: BLE001
                                    logger.warning(
                                        "GraphClaw: failed to import legacy trigger schedule into DB: %s",
                                        exc,
                                    )

                            if loaded_trigger_count == 0:
                                hour_raw = os.environ.get("GRAPHCLAW_BRIEFING_HOUR_UTC", "8")
                                minute_raw = os.environ.get("GRAPHCLAW_BRIEFING_MINUTE_UTC", "0")
                                hour = min(max(int(hour_raw), 0), 23)
                                minute = min(max(int(minute_raw), 0), 59)
                                now = datetime.now(timezone.utc)
                                next_fire = now.replace(
                                    hour=hour,
                                    minute=minute,
                                    second=0,
                                    microsecond=0,
                                )
                                if next_fire <= now:
                                    next_fire += timedelta(days=1)

                                fallback = TriggerConfig(
                                    trigger_id=f"briefing-{trigger_user_id}-{hour:02d}{minute:02d}",
                                    trigger_type=TriggerType.TIME_BASED,
                                    user_id=trigger_user_id,
                                    enabled=True,
                                    cron_expression=f"{minute} {hour} * * *",
                                    next_fire_at=next_fire,
                                    payload_template={"agent_id": agent_id, "briefing": True},
                                )
                                scheduler.register(fallback)
                                loaded_trigger_count = 1
                                logger.info(
                                    "GraphClaw: registered fallback daily briefing trigger (%02d:%02d UTC)",
                                    hour,
                                    minute,
                                )

                                if app.state.graph_store is not None:
                                    try:
                                        await save_trigger_schedule(
                                            app.state.graph_store,
                                            trigger_user_id,
                                            [fallback],
                                            caller_context=_trigger_ctx,
                                        )
                                    except Exception as exc:  # noqa: BLE001
                                        logger.warning(
                                            "GraphClaw: failed to persist fallback trigger schedule to DB: %s",
                                            exc,
                                        )

                        _trigger_engine = TriggerEngine(broker=broker, scheduler=scheduler)
                        await _trigger_engine.start()
                        app.state.trigger_engine = _trigger_engine
                        logger.info(
                            "GraphClaw: trigger engine started (%d scheduled trigger(s))",
                            loaded_trigger_count,
                        )
                except Exception as exc:  # noqa: BLE001
                    logger.error(
                        "GraphClaw: agent loop/consumer initialisation failed — %s",
                        exc,
                        exc_info=exc,
                    )

        except Exception as exc:  # noqa: BLE001
            logger.error("GraphClaw: service initialisation error — %s", exc, exc_info=exc)

        logger.info("GraphClaw Gateway started")
        yield

        # ── Shutdown ──────────────────────────────────────────────────────
        if _trigger_engine is not None:
            await _trigger_engine.stop()
            logger.info("GraphClaw: trigger engine stopped")
        if _agent_event_consumer is not None:
            await _agent_event_consumer.stop()
            logger.info("GraphClaw: agent event consumer stopped")
        if _agent_health_monitor is not None:
            await _agent_health_monitor.stop()
            logger.info("GraphClaw: agent health monitor stopped")
        if _sub_agent_pool is not None:
            await _sub_agent_pool.stop()
            logger.info("GraphClaw: sub-agent pool stopped")
        await registry.stop_all()

        if broker is not None:
            await broker.close()

        if _db_pool is not None:
            await _db_pool.close()
            logger.info("GraphClaw: database pool closed")

        # Clean up deps module singletons
        await shutdown_services()

        logger.info("GraphClaw Gateway shut down")

    app = FastAPI(
        title="GraphClaw Gateway",
        description=(
            "Channel gateway for the GraphClaw task graph orchestration system. "
            "Accepts inbound messages (email, API, CLI), queues outbound "
            "notifications, and exposes health/readiness probes."
        ),
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        openapi_tags=[
            {
                "name": "health",
                "description": "Liveness and readiness probes for orchestrators and load balancers.",
            },
            {
                "name": "inbound",
                "description": "Accept normalised inbound messages from any channel (email, API, CLI).",
            },
            {
                "name": "outbound",
                "description": "Queue outbound messages for delivery via email or other channels.",
            },
            {
                "name": "triggers",
                "description": "On-demand trigger endpoint for ad-hoc agent activations.",
            },
            {
                "name": "auth",
                "description": "OAuth 2.0 + Platform JWT authentication (login, callback, refresh, logout, me).",
            },
            {
                "name": "a2a",
                "description": (
                    "Agent-to-Agent (A2A) REST API — agent key management "
                    "(register, rotate, revoke, list) and the inbound task-update endpoint."
                ),
            },
            {
                "name": "app-api",
                "description": "Application settings and management API",
            },
            {
                "name": "webhooks",
                "description": ("Inbound webhooks from third-party services (SES Lambda, etc.)."),
            },
        ],
        contact={
            "name": "GraphClaw",
            "url": "https://graphclaw.ai",
        },
        license_info={
            "name": "Apache-2.0",
            "identifier": "Apache-2.0",
            "url": "https://www.apache.org/licenses/LICENSE-2.0",
        },
    )

    # ── Middleware ────────────────────────────────────────────────────────
    # Rate limiting: applied after CORS so preflight OPTIONS requests are not
    # counted against caller quotas.  Redis URL is read from the environment
    # so that tests can override it without patching the module.
    app.add_middleware(
        RateLimitMiddleware,
        redis_url=os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
    )

    # JWT Role middleware: decodes the Bearer token (if present) and sets
    # request.state.user_role from the `role` claim.  This allows require_admin
    # to work without a DB round-trip.  Falls back to "USER" if token is absent
    # or the claim is not present.
    from starlette.middleware.base import BaseHTTPMiddleware

    class JWTRoleMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            request.state.user_role = "USER"
            auth_header = request.headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                token = auth_header[7:]
                try:
                    from graphclaw.auth.middleware import get_jwt_service

                    svc = get_jwt_service()
                    payload = svc.verify_token(token)
                    role = payload.get("role", "USER")
                    request.state.user_role = role
                except Exception:  # noqa: BLE001
                    pass  # invalid token — role stays USER, auth will reject at route level
            return await call_next(request)

    app.add_middleware(JWTRoleMiddleware)

    # Logging middleware: sets session_id ContextVar, logs every HTTP request.
    # Added after JWTRoleMiddleware so request.state.user_role is available.
    from graphclaw.infra.logging.middleware import LoggingMiddleware

    app.add_middleware(LoggingMiddleware)

    # ── Include sub-routers (Swagger-documented API) ─────────────────────
    from graphclaw.a2a.routes import a2a_router, task_update_router
    from graphclaw.api.router import app_router
    from graphclaw.auth.routes import router as auth_router
    from graphclaw.gateway.routes.inbound import router as inbound_router
    from graphclaw.gateway.routes.outbound import router as outbound_router

    app.include_router(inbound_router, prefix="/api/v1", tags=["inbound"])
    app.include_router(outbound_router, prefix="/api/v1", tags=["outbound"])
    app.include_router(auth_router)
    # A2A: management endpoints under /api/v1/a2a and inbound task-update at /api/v1/task-update
    app.include_router(a2a_router)
    app.include_router(task_update_router)
    app.include_router(app_router)

    # ── Health routes ──────────────────────────────────────────────────────
    # Inline health routes that return the format expected by Docker health
    # checks and existing tests.  The sub-router health endpoints serve
    # the Swagger-documented /health and /ready paths.

    @app.get("/health", tags=["health"])
    async def health() -> dict[str, str]:
        """Liveness probe — always returns 200 when the process is alive."""
        return {"status": "ok", "service": "gateway"}

    @app.get("/health/ready", tags=["health"])
    async def readiness(request: Request) -> JSONResponse:
        """Readiness probe — reports critical dependency readiness."""
        try:
            is_ready, dependencies = _compute_readiness(request.app)
            startup_mode = getattr(request.app.state, "startup_mode", "degraded")
            status_code = 200 if is_ready else 503
            status = "ready" if is_ready else "degraded"
            return JSONResponse(
                status_code=status_code,
                content={
                    "status": status,
                    "mode": startup_mode,
                    "dependencies": dependencies,
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Readiness check failed", exc_info=exc)
            return JSONResponse(
                status_code=503,
                content={"status": "unavailable", "reason": str(exc)},
            )

    @app.get("/ready", tags=["health"])
    async def readiness_alt(request: Request) -> JSONResponse:
        """Readiness probe (alternative path) — checks broker connectivity."""
        return await readiness(request)

    # ── Inbound route (inline, uses app.state.broker) ──────────────────────

    @app.post("/api/v1/inbound", status_code=202, tags=["inbound"])
    async def receive_inbound(message: InboundMessage, request: Request) -> dict[str, str]:
        """Accept a normalized inbound message and publish it to the broker queue.

        Returns HTTP 202 Accepted immediately; downstream processing is asynchronous.
        """
        current_broker: MessageBroker | None = getattr(request.app.state, "broker", None)
        if current_broker is not None:
            await current_broker.publish(INBOUND_MESSAGES, message.model_dump_json())
            logger.info(
                "Gateway: published inbound message",
                extra={
                    "message_id": message.message_id,
                    "channel": message.channel,
                    "session_id": message.session_id,
                },
            )
        else:
            logger.warning(
                "Gateway: broker not configured, message %s dropped",
                message.message_id,
            )
        return {"status": "accepted", "message_id": message.message_id}

    @app.post("/api/v1/trigger", status_code=202, tags=["triggers"])
    async def on_demand_trigger(payload: dict[str, Any], request: Request) -> dict[str, str]:
        """On-demand trigger endpoint for ad-hoc agent activations.

        Wraps the payload in an ``InboundMessage`` with ``channel="api"`` and
        publishes it to the ``INBOUND_MESSAGES`` queue.
        """
        import json

        message_id = str(uuid.uuid4())
        session_id = f"SES-{uuid.uuid4()}"
        trigger_msg = InboundMessage(
            message_id=message_id,
            channel="api",
            sender=payload.get("sender", "api"),
            subject=payload.get("subject", "trigger"),
            body=json.dumps(payload),
            received_at=datetime.now(tz=timezone.utc),
            session_id=session_id,
        )
        current_broker: MessageBroker | None = getattr(request.app.state, "broker", None)
        if current_broker is not None:
            await current_broker.publish(INBOUND_MESSAGES, trigger_msg.model_dump_json())
            logger.info(
                "Gateway: published trigger message",
                extra={"message_id": message_id, "session_id": session_id},
            )
        else:
            logger.warning("Gateway: broker not configured, trigger %s dropped", message_id)
        return {"status": "accepted", "message_id": message_id}

    # ── WhatsApp webhook routes ──────────────────────────────────────────────

    @app.get("/webhooks/whatsapp", tags=["inbound"])
    async def whatsapp_verify(request: Request) -> Any:
        """Handle Meta's webhook verification challenge (GET).

        Meta sends a GET request with ``hub.mode=subscribe``,
        ``hub.verify_token=<token>``, and ``hub.challenge=<challenge>``.
        We echo back ``hub.challenge`` if the token matches.
        """
        from fastapi.responses import PlainTextResponse

        params = request.query_params
        mode = params.get("hub.mode", "")
        token = params.get("hub.verify_token", "")
        challenge = params.get("hub.challenge", "")

        if mode != "subscribe":
            return JSONResponse(status_code=400, content={"error": "invalid hub.mode"})

        registry = getattr(request.app.state, "registry", None)
        adapter = registry.get("whatsapp") if registry else None
        if adapter is None:
            return JSONResponse(
                status_code=503, content={"error": "whatsapp channel not configured"}
            )

        if not adapter.verify_webhook_token(token):
            logger.warning("WhatsApp webhook verification failed: bad verify_token")
            return JSONResponse(status_code=403, content={"error": "forbidden"})

        logger.info("WhatsApp webhook verified successfully")
        return PlainTextResponse(content=challenge)

    @app.post("/webhooks/whatsapp", status_code=200, tags=["inbound"])
    async def whatsapp_inbound(request: Request) -> Any:
        """Receive and process a WhatsApp Cloud API webhook event (POST).

        Validates the ``X-Hub-Signature-256`` header before processing.
        Returns ``{"status": "ok"}`` to Meta immediately; message processing
        is asynchronous via the broker.
        """
        body_bytes = await request.body()
        signature = request.headers.get("X-Hub-Signature-256", "")

        registry = getattr(request.app.state, "registry", None)
        adapter = registry.get("whatsapp") if registry else None
        if adapter is None:
            # Channel not configured — accept silently so Meta doesn't retry
            return {"status": "ok"}

        if not adapter.verify_signature(body_bytes, signature):
            logger.warning("WhatsApp inbound: invalid signature, rejecting webhook")
            return JSONResponse(status_code=403, content={"error": "invalid signature"})

        import json as _json

        try:
            payload = _json.loads(body_bytes)
        except _json.JSONDecodeError:
            return JSONResponse(status_code=400, content={"error": "invalid JSON"})

        await adapter.handle_webhook(payload)
        return {"status": "ok"}

    # ── Telegram webhook route ────────────────────────────────────────────────

    @app.post("/webhooks/telegram", status_code=200, tags=["inbound"])
    async def telegram_inbound(request: Request) -> Any:
        """Receive a Telegram Update via webhook (POST).

        Validates the optional ``X-Telegram-Bot-Api-Secret-Token`` header
        if ``TELEGRAM_WEBHOOK_SECRET`` is set. Returns ``{"status": "ok"}``
        immediately; message processing is asynchronous via the broker.

        Only active when ``TELEGRAM_USE_WEBHOOK=true``.
        """
        secret_token = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")

        registry = getattr(request.app.state, "registry", None)
        adapter = registry.get("telegram") if registry else None
        if adapter is None:
            return {"status": "ok"}

        if not adapter.verify_secret_token(secret_token):
            logger.warning("Telegram inbound: invalid secret token, rejecting webhook")
            return JSONResponse(status_code=403, content={"error": "forbidden"})

        import json as _json

        body_bytes = await request.body()
        try:
            update = _json.loads(body_bytes)
        except _json.JSONDecodeError:
            return JSONResponse(status_code=400, content={"error": "invalid JSON"})

        await adapter.handle_update(update)
        return {"status": "ok"}

    # ── Slack webhook routes ──────────────────────────────────────────────────

    @app.post("/webhooks/slack", status_code=200, tags=["inbound"])
    async def slack_inbound(request: Request) -> Any:
        """Receive a Slack Events API callback (POST).

        Verifies the ``X-Slack-Signature`` header before processing.
        Handles Slack URL verification challenges transparently.
        Returns ``{"status": "ok"}`` immediately; message processing is
        asynchronous via the broker.
        """
        import json as _json

        body_bytes = await request.body()
        timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
        signature = request.headers.get("X-Slack-Signature", "")

        registry = getattr(request.app.state, "registry", None)
        adapter = registry.get("slack") if registry else None
        if adapter is None:
            # Channel not configured — accept silently so Slack does not retry
            return {"status": "ok"}

        # Verify signature only when signing_secret is configured
        if not adapter.verify_webhook_signature(body_bytes, timestamp, signature):
            logger.warning("Slack inbound: invalid signature, rejecting webhook")
            return JSONResponse(status_code=403, content={"error": "invalid signature"})

        try:
            payload = _json.loads(body_bytes)
        except _json.JSONDecodeError:
            return JSONResponse(status_code=400, content={"error": "invalid JSON"})

        # Respond to Slack URL verification challenge
        if payload.get("type") == "url_verification":
            return {"challenge": payload.get("challenge", "")}

        await adapter.handle_webhook(payload)
        return {"status": "ok"}

    # ── SES inbound email webhook ─────────────────────────────────────────────

    @app.post("/webhooks/email/ses", tags=["webhooks"])
    async def ses_email_webhook(request: Request) -> Any:
        """SES inbound email via Lambda → Gateway POST.

        Accepts a JSON payload from the Lambda function that is triggered by
        SES receipt actions. Verifies the HMAC-SHA256 ``X-GraphClaw-Signature``
        header, downloads the raw email from S3, normalises it to an
        ``InboundMessage``, and publishes it to the broker.

        Replaces IMAP polling in production (EMAIL_BACKEND=ses).
        Local dev continues to use the IMAP poller (EMAIL_BACKEND=imap).
        """
        from fastapi import HTTPException

        body = await request.body()
        signature = request.headers.get("X-GraphClaw-Signature", "")

        import json as _json

        try:
            payload = _json.loads(body)
        except _json.JSONDecodeError:
            return JSONResponse(status_code=400, content={"error": "invalid JSON"})

        receiver = SESEmailReceiver.from_env()

        if not receiver.verify_lambda_signature(body, signature):
            logger.warning("SES webhook: invalid Lambda signature, rejecting request")
            raise HTTPException(status_code=403, detail="Invalid Lambda signature")

        msg = await receiver.handle_ses_notification(payload)
        if msg is None:
            return {"status": "skipped"}

        # Publish to broker (same pipeline as IMAP)
        current_broker: MessageBroker | None = getattr(request.app.state, "broker", None)
        if current_broker is not None:
            await current_broker.publish(INBOUND_MESSAGES, msg.model_dump_json())
            logger.info(
                "SES webhook: published inbound message",
                extra={
                    "message_id": msg.message_id,
                    "channel": msg.channel,
                    "session_id": msg.session_id,
                },
            )
        else:
            logger.warning(
                "SES webhook: broker not configured, message %s dropped",
                msg.message_id,
            )
        return {"status": "accepted"}

    # ── Teams webhook route ───────────────────────────────────────────────────

    @app.post("/webhooks/teams", status_code=200, tags=["inbound"])
    async def teams_inbound(request: Request) -> Any:
        """Receive a Microsoft Teams Bot Framework Activity (POST).

        Returns ``{"status": "ok"}`` immediately; message processing is
        asynchronous via the broker.
        """
        import json as _json

        body_bytes = await request.body()

        registry = getattr(request.app.state, "registry", None)
        adapter = registry.get("teams") if registry else None
        if adapter is None:
            # Channel not configured — accept silently
            return {"status": "ok"}

        try:
            payload = _json.loads(body_bytes)
        except _json.JSONDecodeError:
            return JSONResponse(status_code=400, content={"error": "invalid JSON"})

        await adapter.handle_activity(payload)
        return {"status": "ok"}

    return app


# Module-level ASGI app instance for uvicorn (Dockerfile CMD: uvicorn graphclaw.gateway.app:app)
app = create_app()
