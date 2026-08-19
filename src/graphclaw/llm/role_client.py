# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""graphclaw.llm.role_client — Decorator binding an LLMClient to one LLMRole.

Description
-----------
``RoleBoundLLMClient`` wraps a shared base ``LLMClient`` (one per provider,
owned by :class:`~graphclaw.llm.routing.ModelRouter`) and supplies that
role's configured model whenever a call site omits ``model=``. Call sites
that already pass ``model=None`` need no change at all: ``None`` used to
mean "the client's single default"; it now means "this role's default".

An explicit ``model=`` argument (e.g. a per-agent ``config.json.llm_model``
override) passes straight through, untouched.

Design Patterns
---------------
- Decorator: implements the same ``LLMClient`` ABC as the client it wraps,
  so every existing call site keeps working without knowing routing exists.

Public API
----------
- RoleBoundLLMClient: the decorator.

Dependencies
------------
- graphclaw.llm.base: LLMClient ABC and shared data models.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from graphclaw.llm.base import (
    LLMClient,
    LLMMessage,
    LLMResponse,
    LLMStreamChunk,
    ToolDefinition,
)

if TYPE_CHECKING:
    from graphclaw.llm.roles import LLMRole
    from graphclaw.llm.routing import ModelSpec


class RoleBoundLLMClient(LLMClient):
    """Supplies a role's configured model to a shared inner ``LLMClient``.

    Args:
        inner: The shared base client for this role's resolved provider,
            owned by the ``ModelRouter`` that created this facade.
        role: The ``LLMRole`` this facade represents.
        spec: The resolved ``ModelSpec`` (provider + model) for that role.

    Note:
        ``close()`` is intentionally a no-op. Several roles can share one
        inner client (they always do when they resolve to the same
        provider); closing the shared connection from one role's facade
        would break the other five. Only ``ModelRouter.aclose()`` may close
        base clients.
    """

    def __init__(self, inner: LLMClient, role: LLMRole, spec: ModelSpec) -> None:
        self._inner = inner
        self._role = role
        self._spec = spec

    @property
    def role(self) -> LLMRole:
        return self._role

    @property
    def spec(self) -> ModelSpec:
        return self._spec

    async def complete(
        self,
        messages: list[LLMMessage],
        *,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.0,
        tools: list[ToolDefinition] | None = None,
    ) -> LLMResponse:
        return await self._inner.complete(
            messages,
            model=model or self._spec.model,
            max_tokens=max_tokens,
            temperature=temperature,
            tools=tools,
        )

    async def stream(
        self,
        messages: list[LLMMessage],
        *,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.0,
        tools: list[ToolDefinition] | None = None,
    ) -> AsyncIterator[LLMStreamChunk]:
        async for chunk in self._inner.stream(
            messages,
            model=model or self._spec.model,
            max_tokens=max_tokens,
            temperature=temperature,
            tools=tools,
        ):
            yield chunk

    async def count_tokens(
        self,
        messages: list[LLMMessage],
        *,
        model: str | None = None,
    ) -> int:
        return await self._inner.count_tokens(messages, model=model or self._spec.model)

    async def close(self) -> None:
        """No-op — the base client is shared and owned by ModelRouter."""
        return None
