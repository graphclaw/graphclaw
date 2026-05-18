# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for gateway startup LLM provider selection policy."""

from __future__ import annotations

import pytest

from graphclaw.gateway.app import _normalize_default_provider, _select_startup_llm_provider_and_key


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
async def test_selects_default_provider_when_both_keys_present_openai(monkeypatch: pytest.MonkeyPatch):
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
async def test_selects_default_provider_when_both_keys_present_anthropic(monkeypatch: pytest.MonkeyPatch):
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
