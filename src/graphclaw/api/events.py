"""graphclaw.api.events — Server-Sent Events stream for real-time cockpit updates.

Description
-----------
Provides ``GET /app/v1/events`` — a long-lived SSE connection that streams
graph change events to the cockpit UI.  The cockpit uses this to update task
state badges, score ranks, and approval notifications without polling.

Event types published on this stream
--------------------------------------
- ``task.state_changed``  — A task moved to a new TaskState.
- ``task.scored``         — A new scoring pass completed for a task.
- ``briefing.ready``      — A daily briefing has been generated.
- ``approval.pending``    — A new APPROVAL task is waiting for human action.
- ``skill.completed``     — A skill agent finished its execution.
- ``ping``                — Keepalive heartbeat emitted every 30 seconds.

Architecture
------------
Events originate from three producers:

1. The ``StateMachine`` (on every ``transition()`` call) publishes to the
   Redis channel ``graphclaw:events:{user_id}``.
2. The ``ScoringEngine`` (after each ``score_all()`` pass) publishes to the
   same channel.
3. The ``AgentLoop`` (on briefing generation and skill completion) publishes
   to the channel.

This endpoint subscribes to that Redis pub/sub channel and forwards events
as SSE to the browser client.

Graceful degradation
--------------------
If Redis is unavailable (``app.state.redis`` is ``None``), the endpoint still
starts and emits keepalive pings only, so the cockpit remains functional in
dev environments without Redis.

Design Patterns
---------------
- Server-Sent Events: Uses ``StreamingResponse`` with ``text/event-stream``
  content type to push events over HTTP/1.1 without WebSocket overhead.
- Redis pub/sub: Subscribes to a per-user channel so each client only receives
  its own events.
- Generator-based streaming: An async generator yields formatted SSE frames;
  FastAPI's ``StreamingResponse`` handles backpressure.

Public API
----------
- router: ``APIRouter`` for /events routes.

Dependencies
------------
- graphclaw.api.deps: CurrentUserDep.
- fastapi: APIRouter, Request, status (third-party).
- fastapi.responses: StreamingResponse (third-party).
- asyncio: for sleep between keepalives (stdlib).
- json: event serialisation (stdlib).
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from datetime import datetime, timezone

from fastapi import APIRouter, Request, status
from fastapi.responses import StreamingResponse

from graphclaw.api.deps import CurrentUserDep

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/events", tags=["events"])

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_KEEPALIVE_INTERVAL_SECS = 30
_REDIS_CHANNEL_PREFIX = "graphclaw:events:"

# ---------------------------------------------------------------------------
# SSE formatting helpers
# ---------------------------------------------------------------------------


def _sse_event(event_type: str, data: dict) -> str:
    """Format a single SSE frame.

    Parameters
    ----------
    event_type:
        The SSE ``event:`` field value (e.g. ``task.state_changed``).
    data:
        JSON-serialisable dict payload.

    Returns
    -------
    str:
        A properly terminated SSE frame string.
    """
    payload = json.dumps(data, default=str)
    return f"event: {event_type}\ndata: {payload}\n\n"


def _sse_comment(text: str) -> str:
    """Format an SSE comment (used for keepalive pings)."""
    return f": {text}\n\n"


# ---------------------------------------------------------------------------
# Event generators
# ---------------------------------------------------------------------------


async def _stream_with_redis(
    user_id: str,
    redis,
    request: Request,
) -> AsyncGenerator[str, None]:
    """Subscribe to the user's Redis pub/sub channel and yield SSE frames.

    Falls back to keepalive-only if the subscription fails.
    """
    channel = f"{_REDIS_CHANNEL_PREFIX}{user_id}"
    pubsub = redis.pubsub()
    subscribed = False

    try:
        await pubsub.subscribe(channel)
        subscribed = True
        logger.info("events: subscribed user_id=%s on channel=%s", user_id, channel)

        # Emit a connection-confirmed event
        yield _sse_event(
            "connected",
            {
                "user_id": user_id,
                "channel": channel,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

        last_ping = asyncio.get_event_loop().time()

        while True:
            # Check if the client disconnected.
            if await request.is_disconnected():
                logger.info("events: client disconnected user_id=%s", user_id)
                break

            # Non-blocking check for a new message (100 ms timeout).
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.1)

            if message and message.get("type") == "message":
                raw = message.get("data", b"")
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", errors="replace")
                try:
                    payload = json.loads(raw)
                    event_type = payload.pop("event_type", "event")
                    yield _sse_event(event_type, payload)
                except json.JSONDecodeError:
                    logger.warning("events: invalid JSON on channel=%s: %r", channel, raw)

            # Emit a keepalive ping every 30 seconds.
            now = asyncio.get_event_loop().time()
            if now - last_ping >= _KEEPALIVE_INTERVAL_SECS:
                yield _sse_comment("ping")
                last_ping = now
            else:
                # Yield control to the event loop briefly.
                await asyncio.sleep(0.05)

    except Exception as exc:
        logger.error("events: stream error for user_id=%s: %s", user_id, exc)
        yield _sse_event("error", {"detail": "Stream error; reconnect to resume."})
    finally:
        if subscribed:
            try:
                await pubsub.unsubscribe(channel)
                await pubsub.close()
            except Exception:
                pass


async def _stream_keepalive_only(
    user_id: str,
    request: Request,
) -> AsyncGenerator[str, None]:
    """Emit keepalive pings only (used when Redis is unavailable)."""
    logger.warning(
        "events: Redis unavailable — emitting keepalive-only stream for user_id=%s",
        user_id,
    )
    yield _sse_event(
        "connected",
        {
            "user_id": user_id,
            "mode": "keepalive_only",
            "reason": "Redis is not configured; live events are unavailable.",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )

    while True:
        if await request.is_disconnected():
            break
        yield _sse_comment("ping")
        await asyncio.sleep(_KEEPALIVE_INTERVAL_SECS)


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


@router.get(
    "",
    status_code=status.HTTP_200_OK,
    summary="Real-time event stream (SSE)",
    description=(
        "Open a Server-Sent Events connection to receive real-time graph change "
        "notifications.  Events are scoped to the authenticated user. "
        "If Redis is unavailable the stream emits keepalive pings only."
    ),
)
async def event_stream(
    request: Request,
    user_id: CurrentUserDep,
) -> StreamingResponse:
    """Return an SSE ``StreamingResponse`` for the authenticated user."""
    redis = getattr(request.app.state, "redis", None)

    if redis is not None:
        generator = _stream_with_redis(user_id, redis, request)
    else:
        generator = _stream_keepalive_only(user_id, request)

    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # Disable Nginx buffering
            "Connection": "keep-alive",
        },
    )
