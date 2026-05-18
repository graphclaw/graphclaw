# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.a2a.models — Data models for the A2A (Agent-to-Agent) REST API.

Description
-----------
Defines the Pydantic and dataclass models used by the A2A key management and
inbound task-update pipeline.  These models carry no business logic — they are
pure DTOs and value objects for serialization, validation, and typed transport.

Design Patterns
---------------
- Frozen Dataclass: ``A2AKeyRef`` is frozen to enforce immutability of key
  reference objects once issued.  Key metadata should never be mutated in
  place; rotation creates a new reference.
- Pydantic Models: ``A2ARegistration``, ``A2AUpdatePayload``, and
  ``A2AKeyRotateResponse`` use Pydantic v2 for automatic validation and
  JSON serialization in FastAPI route handlers.
- JSON-RPC 2.0 wrapper: ``A2AUpdatePayload`` mirrors the JSON-RPC 2.0 envelope
  so agent callers can use standard JSON-RPC client libraries.

Public API
----------
- A2AKeyRef: Frozen dataclass carrying key metadata (never the plaintext key).
- A2ARegistration: Request body for registering a new agent key.
- A2AUpdatePayload: JSON-RPC 2.0 envelope for inbound task-update messages.
- A2AKeyRotateResponse: Response body for key rotation (one-time plaintext).

Dependencies
------------
- dataclasses: dataclass, field (stdlib).
- datetime: datetime (stdlib).
- pydantic: BaseModel, Field (third-party).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

# ── A2AKeyRef ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class A2AKeyRef:
    """Immutable reference to a registered A2A agent key.

    Contains all metadata about a key except the plaintext secret, which is
    shown exactly once at registration/rotation and never stored.

    Attributes
    ----------
    key_id:
        Unique identifier for this key (same as the resource node ID in the
        graph, e.g. ``RES-{uuid4}``).
    agent_name:
        Human-readable name for the agent that owns this key.
    user_id:
        Platform user ID (``USER-{uuid}``) of the user who registered the agent.
    created_at:
        timezone.utc timestamp when this key was first issued.
    resource_node_id:
        Graph node ID for the ``ResourceNode`` backing this agent registration.
    """

    key_id: str
    agent_name: str
    user_id: str
    created_at: datetime
    resource_node_id: str


# ── A2ARegistration ───────────────────────────────────────────────────────────


class A2ARegistration(BaseModel):
    """Request body for ``POST /api/v1/a2a/agents``.

    Attributes
    ----------
    agent_name:
        Human-readable identifier for the agent being registered.  Must be
        unique per user; used in log entries and the agent list response.
    description:
        Optional free-text description of what the agent does.
    callback_url:
        Optional HTTPS URL the platform may use to push notifications back to
        the agent.  Currently stored for future use; not validated beyond
        basic string type.
    """

    agent_name: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="Human-readable name for the agent being registered.",
    )
    description: str = Field(
        default="",
        max_length=512,
        description="Optional description of the agent's purpose.",
    )
    callback_url: str | None = Field(
        default=None,
        description="Optional URL for platform-to-agent push notifications.",
    )


# ── A2AUpdatePayload ──────────────────────────────────────────────────────────


class A2AUpdatePayload(BaseModel):
    """JSON-RPC 2.0 envelope for inbound ``POST /api/v1/task-update`` messages.

    Agents MUST send a JSON body conforming to this model.  The ``method``
    field identifies the operation (e.g. ``"task.update"``, ``"task.complete"``).
    The ``params`` dict carries the operation-specific payload and is passed
    verbatim to the agent loop via the broker queue.

    Attributes
    ----------
    jsonrpc:
        JSON-RPC protocol version string.  Must be ``"2.0"``.
    method:
        The JSON-RPC method name (e.g. ``"task.update"``).
    params:
        Operation-specific parameters.  Forwarded as-is to the agent loop.
    id:
        Optional JSON-RPC request ID for correlation.  Not used server-side
        for async processing but echoed in error responses when present.
    """

    jsonrpc: str = Field(
        default="2.0",
        description="JSON-RPC protocol version. Must be '2.0'.",
    )
    method: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="JSON-RPC method name, e.g. 'task.update'.",
    )
    params: dict[str, Any] = Field(
        default_factory=dict,
        description="Method-specific parameters forwarded to the agent loop.",
    )
    id: str | None = Field(
        default=None,
        description="Optional JSON-RPC request ID for client-side correlation.",
    )


# ── A2AKeyRotateResponse ──────────────────────────────────────────────────────


class A2AKeyRotateResponse(BaseModel):
    """Response body for ``POST /api/v1/a2a/agents/{key_id}/rotate``.

    The ``plaintext_key`` is shown exactly once.  The caller must store it
    securely; subsequent calls to the rotate endpoint will invalidate this key.

    Attributes
    ----------
    key_id:
        The resource node ID identifying the agent registration.
    plaintext_key:
        The new API key in ``wg_agent_*`` format.  Shown once; never stored.
    rotated_at:
        timezone.utc timestamp when the rotation was performed.
    """

    key_id: str = Field(..., description="Resource node ID of the agent registration.")
    plaintext_key: str = Field(
        ...,
        description="New plaintext API key. Shown once — store securely.",
    )
    rotated_at: datetime = Field(
        ...,
        description="timezone.utc timestamp when the key was rotated.",
    )
