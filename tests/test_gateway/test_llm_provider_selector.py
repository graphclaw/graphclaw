# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for gateway startup LLM provider selection policy."""

from __future__ import annotations

import pytest

from graphclaw.gateway.app import _normalize_default_provider, _select_startup_llm_provider_and_key

# Role-routing env vars that make _select_startup_llm_provider_and_key prefer
# litellm at priority 1 (see the function's docstring). Local dev environments
# (e.g. a running Ollama container) commonly export LITELLM_DEFAULT_MODEL for
# real use, which would otherwise leak into these tests and make the
# Anthropic/OpenAI-selection assertions below fail nondeterministically.
_ROLE_ROUTING_ENV_VARS = (
    "LITELLM_DEFAULT_MODEL",
    "GRAPHCLAW_MODEL_DEFAULT",
    "GRAPHCLAW_MODEL_ORCHESTRATOR",
    "GRAPHCLAW_MODEL_SUBAGENT",
    "GRAPHCLAW_MODEL_SKILL",
    "GRAPHCLAW_MODEL_DISTILL",
    "GRAPHCLAW_MODEL_CLASSIFY",
    "GRAPHCLAW_MODEL_SUMMARIZE",
)


@pytest.fixture(autouse=True)
def _clear_role_routing_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _ROLE_ROUTING_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


class _FakeSecretsClient:
    def __init__(self, values: dict[str, str]) -> None:
        self._values = values

    async def get_secret(self, key: str) -> str:
        if key not in self._values:
            raise KeyError(key)
        return self._values[key]


@pytest.mark.asyncio
async def test_selects_anthropic_when_only_anthropic_key_present(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("GRAPHCLAW_DEFAULT_LLM_PROVIDER", raising=False)
    secrets = _FakeSecretsClient({"ANTHROPIC_API_KEY": "ant-key"})

    provider, key = await _select_startup_llm_provider_and_key(secrets)

    assert provider == "anthropic"
    assert key == "ant-key"


@pytest.mark.asyncio
async def test_selects_openai_when_only_openai_key_present(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("GRAPHCLAW_DEFAULT_LLM_PROVIDER", raising=False)
    secrets = _FakeSecretsClient({"OPENAI_API_KEY": "oa-key"})

    provider, key = await _select_startup_llm_provider_and_key(secrets)

    assert provider == "openai"
    assert key == "oa-key"


@pytest.mark.asyncio
async def test_selects_default_provider_when_both_keys_present_openai(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("GRAPHCLAW_DEFAULT_LLM_PROVIDER", "openai")
    secrets = _FakeSecretsClient(
        {
            "ANTHROPIC_API_KEY": "ant-key",
            "OPENAI_API_KEY": "oa-key",
        }
    )

    provider, key = await _select_startup_llm_provider_and_key(secrets)

    assert provider == "openai"
    assert key == "oa-key"


@pytest.mark.asyncio
async def test_selects_default_provider_when_both_keys_present_anthropic(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("GRAPHCLAW_DEFAULT_LLM_PROVIDER", "anthropic")
    secrets = _FakeSecretsClient(
        {
            "ANTHROPIC_API_KEY": "ant-key",
            "OPENAI_API_KEY": "oa-key",
        }
    )

    provider, key = await _select_startup_llm_provider_and_key(secrets)

    assert provider == "anthropic"
    assert key == "ant-key"


@pytest.mark.asyncio
async def test_returns_none_when_no_keys_present(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("GRAPHCLAW_DEFAULT_LLM_PROVIDER", raising=False)
    secrets = _FakeSecretsClient({})

    provider, key = await _select_startup_llm_provider_and_key(secrets)

    assert provider is None
    assert key is None


def test_invalid_default_provider_falls_back_to_anthropic():
    assert _normalize_default_provider("invalid") == "anthropic"


@pytest.mark.asyncio
async def test_selects_litellm_when_litellm_default_model_set(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LITELLM_DEFAULT_MODEL", "ollama/qwen2.5:7b")
    secrets = _FakeSecretsClient({"ANTHROPIC_API_KEY": "ant-key"})

    provider, _key = await _select_startup_llm_provider_and_key(secrets)

    assert provider == "litellm"


@pytest.mark.asyncio
async def test_selects_litellm_when_only_a_role_routing_var_is_set(
    monkeypatch: pytest.MonkeyPatch,
):
    """A role-only setup (GRAPHCLAW_MODEL_ORCHESTRATOR, no LITELLM_DEFAULT_MODEL)
    must still select litellm — otherwise startup would pick anthropic/openai
    and send an ollama/ model string to a direct SDK."""
    monkeypatch.setenv("GRAPHCLAW_MODEL_ORCHESTRATOR", "ollama/qwen2.5:14b")
    secrets = _FakeSecretsClient({"ANTHROPIC_API_KEY": "ant-key"})

    provider, _key = await _select_startup_llm_provider_and_key(secrets)

    assert provider == "litellm"


@pytest.mark.asyncio
async def test_selects_anthropic_when_no_role_routing_var_is_set(
    monkeypatch: pytest.MonkeyPatch,
):
    """Sanity check that the broadened priority-1 check does not fire when
    nothing role-routing related is configured at all."""
    secrets = _FakeSecretsClient({"ANTHROPIC_API_KEY": "ant-key"})

    provider, _key = await _select_startup_llm_provider_and_key(secrets)

    assert provider == "anthropic"
