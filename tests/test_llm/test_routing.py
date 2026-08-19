# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""tests.test_llm.test_routing — Tests for LLMRoutingConfig, ModelRouter, RoleBoundLLMClient.

Covers the role-based model routing layer added so the orchestrator,
sub-agents, skill workers, distiller, classifier, and summarizer can each
run on an independently configured model while defaulting to today's
single-model behaviour when only LITELLM_DEFAULT_MODEL is set.
"""

from __future__ import annotations

import dataclasses

import pytest

from graphclaw.config import LLMRoutingConfig, config
from graphclaw.llm.base import LLMClient, LLMMessage, LLMResponse, LLMStreamChunk
from graphclaw.llm.roles import LLMRole
from graphclaw.llm.routing import ModelRouter

pytestmark = pytest.mark.usefixtures("_clear_llm_role_env")


@pytest.fixture
def _clear_llm_role_env(monkeypatch):
    """Ensure no host/CI environment leaks into these tests."""
    names = [
        "GRAPHCLAW_MODEL_DEFAULT",
        "GRAPHCLAW_MODEL_PROVIDER_DEFAULT",
        "LITELLM_DEFAULT_MODEL",
        "GRAPHCLAW_DISTILLATION_MODEL",
        "INTELLIGENCE_AGENT_MODEL",
        "GRAPHCLAW_PROFILE_SYNTHESIS_MODEL",
    ]
    for role in ("ORCHESTRATOR", "SUBAGENT", "SKILL", "DISTILL", "CLASSIFY", "SUMMARIZE"):
        names.append(f"GRAPHCLAW_MODEL_{role}")
        names.append(f"GRAPHCLAW_MODEL_PROVIDER_{role}")
    for name in names:
        monkeypatch.delenv(name, raising=False)


class _FakeLLMClient(LLMClient):
    """Minimal in-memory LLMClient recording every call for assertions."""

    def __init__(self) -> None:
        self.complete_calls: list[dict] = []
        self.stream_calls: list[dict] = []
        self.count_tokens_calls: list[dict] = []
        self.closed = False

    async def complete(self, messages, *, model=None, max_tokens=4096, temperature=0.0, tools=None):
        self.complete_calls.append({"model": model, "max_tokens": max_tokens, "tools": tools})
        return LLMResponse(
            content="ok",
            model=model or "unset",
            tokens_used=1,
            prompt_tokens=1,
            completion_tokens=0,
            cost_usd=0.0,
        )

    async def stream(self, messages, *, model=None, max_tokens=4096, temperature=0.0, tools=None):
        self.stream_calls.append({"model": model})
        yield LLMStreamChunk(content_delta="ok", is_final=False)
        yield LLMStreamChunk(
            is_final=True,
            accumulated=LLMResponse(
                content="ok",
                model=model or "unset",
                tokens_used=1,
                prompt_tokens=1,
                completion_tokens=0,
                cost_usd=0.0,
            ),
        )

    async def count_tokens(self, messages, *, model=None):
        self.count_tokens_calls.append({"model": model})
        return 1

    async def close(self):
        self.closed = True


def _counting_factory(created: list[str]):
    def factory(provider: str, **kwargs):
        created.append(provider)
        return _FakeLLMClient()

    return factory


# ---------------------------------------------------------------------------
# LLMRoutingConfig — resolution chain
# ---------------------------------------------------------------------------


def test_all_roles_fall_back_to_litellm_default(monkeypatch):
    """Backward-compat guarantee: with only LITELLM_DEFAULT_MODEL set, every
    role resolves to the same model."""
    monkeypatch.setenv("LITELLM_DEFAULT_MODEL", "ollama/qwen2.5:7b")
    policy = LLMRoutingConfig.from_env()

    for role in ("orchestrator", "subagent", "skill", "distill", "classify", "summarize"):
        assert policy.model_for(role) == "ollama/qwen2.5:7b"


def test_role_env_overrides_default(monkeypatch):
    monkeypatch.setenv("LITELLM_DEFAULT_MODEL", "ollama/qwen2.5:7b")
    monkeypatch.setenv("GRAPHCLAW_MODEL_SUBAGENT", "ollama/qwen2.5:14b")
    policy = LLMRoutingConfig.from_env()

    assert policy.model_for("subagent") == "ollama/qwen2.5:14b"
    assert policy.model_for("orchestrator") == "ollama/qwen2.5:7b"


def test_graphclaw_model_default_overrides_litellm_default(monkeypatch):
    monkeypatch.setenv("LITELLM_DEFAULT_MODEL", "claude-sonnet-4-20250514")
    monkeypatch.setenv("GRAPHCLAW_MODEL_DEFAULT", "ollama/qwen2.5:7b")
    policy = LLMRoutingConfig.from_env()

    assert policy.model_for("orchestrator") == "ollama/qwen2.5:7b"


def test_legacy_distillation_model_env_honoured_for_distill_and_summarize(monkeypatch):
    monkeypatch.setenv("LITELLM_DEFAULT_MODEL", "ollama/qwen2.5:7b")
    monkeypatch.setenv("GRAPHCLAW_DISTILLATION_MODEL", "ollama/qwen2.5:1.5b")
    policy = LLMRoutingConfig.from_env()

    assert policy.model_for("distill") == "ollama/qwen2.5:1.5b"
    assert policy.model_for("summarize") == "ollama/qwen2.5:1.5b"
    assert policy.model_for("orchestrator") == "ollama/qwen2.5:7b"


def test_legacy_env_loses_to_explicit_role_env(monkeypatch):
    monkeypatch.setenv("LITELLM_DEFAULT_MODEL", "ollama/qwen2.5:7b")
    monkeypatch.setenv("GRAPHCLAW_DISTILLATION_MODEL", "ollama/qwen2.5:1.5b")
    monkeypatch.setenv("GRAPHCLAW_MODEL_DISTILL", "ollama/qwen2.5:3b")
    policy = LLMRoutingConfig.from_env()

    assert policy.model_for("distill") == "ollama/qwen2.5:3b"


def test_intelligence_and_profile_legacy_envs_honoured_for_classify(monkeypatch):
    monkeypatch.setenv("LITELLM_DEFAULT_MODEL", "ollama/qwen2.5:7b")
    monkeypatch.setenv("INTELLIGENCE_AGENT_MODEL", "ollama/qwen2.5:1.5b")
    policy = LLMRoutingConfig.from_env()

    assert policy.model_for("classify") == "ollama/qwen2.5:1.5b"


def test_inline_provider_prefix_parsed(monkeypatch):
    monkeypatch.setenv("LITELLM_DEFAULT_MODEL", "claude-sonnet-4-20250514")
    monkeypatch.setenv("GRAPHCLAW_MODEL_DISTILL", "litellm:ollama/qwen2.5:1.5b")
    policy = LLMRoutingConfig.from_env()

    assert policy.model_for("distill") == "ollama/qwen2.5:1.5b"
    assert policy.provider_for("distill") == "litellm"


def test_provider_default_chain(monkeypatch):
    monkeypatch.setenv("LITELLM_DEFAULT_MODEL", "ollama/qwen2.5:7b")
    monkeypatch.setenv("GRAPHCLAW_MODEL_PROVIDER_DEFAULT", "anthropic")
    monkeypatch.setenv("GRAPHCLAW_MODEL_PROVIDER_SUBAGENT", "openai")
    policy = LLMRoutingConfig.from_env()

    assert policy.provider_for("subagent") == "openai"
    assert policy.provider_for("orchestrator") == "anthropic"
    # No env at all -> caller-supplied default wins.
    assert policy.provider_for("skill", default="litellm") == "anthropic"


def test_provider_for_falls_back_to_caller_default_when_unset(monkeypatch):
    monkeypatch.setenv("LITELLM_DEFAULT_MODEL", "ollama/qwen2.5:7b")
    policy = LLMRoutingConfig.from_env()

    assert policy.provider_for("orchestrator", default="litellm") == "litellm"


def test_config_llm_routing_property_is_not_cached(monkeypatch):
    """Unlike config.app (cached_property), config.llm_routing must observe
    env changes within the same process — required for test isolation."""
    monkeypatch.setenv("LITELLM_DEFAULT_MODEL", "model-a")
    assert config.llm_routing.model_for("orchestrator") == "model-a"

    monkeypatch.setenv("LITELLM_DEFAULT_MODEL", "model-b")
    assert config.llm_routing.model_for("orchestrator") == "model-b"


# ---------------------------------------------------------------------------
# ModelRouter — client cache
# ---------------------------------------------------------------------------


def _policy(**overrides) -> LLMRoutingConfig:
    models = {
        r: "ollama/qwen2.5:7b"
        for r in ("orchestrator", "subagent", "skill", "distill", "classify", "summarize")
    }
    providers = {r: "litellm" for r in models}
    models.update(overrides.get("models", {}))
    providers.update(overrides.get("providers", {}))
    return LLMRoutingConfig(models=models, providers=providers)


def test_single_base_client_for_all_roles_same_provider():
    created: list[str] = []
    router = ModelRouter(_policy(), client_factory=_counting_factory(created))

    for role in LLMRole:
        router.for_role(role)

    assert created == ["litellm"]


def test_distinct_provider_creates_second_base_client():
    created: list[str] = []
    policy = _policy(providers={"subagent": "anthropic"}, models={"subagent": "claude-sonnet-4-6"})
    router = ModelRouter(policy, client_factory=_counting_factory(created))

    router.for_role(LLMRole.ORCHESTRATOR)
    router.for_role(LLMRole.SUBAGENT)
    router.for_role(LLMRole.SKILL)

    assert sorted(created) == ["anthropic", "litellm"]


def test_for_role_returns_cached_facade():
    router = ModelRouter(_policy(), client_factory=_counting_factory([]))
    assert router.for_role(LLMRole.ORCHESTRATOR) is router.for_role(LLMRole.ORCHESTRATOR)


async def test_aclose_closes_each_base_once():
    fakes: list[_FakeLLMClient] = []

    def factory(provider: str, **kwargs):
        fake = _FakeLLMClient()
        fakes.append(fake)
        return fake

    router = ModelRouter(_policy(), client_factory=factory)
    router.for_role(LLMRole.ORCHESTRATOR)
    router.for_role(LLMRole.SUBAGENT)
    router.for_role(LLMRole.SKILL)

    await router.aclose()

    assert len(fakes) == 1  # one shared litellm base client
    assert fakes[0].closed is True


async def test_role_client_close_is_noop():
    fake = _FakeLLMClient()
    router = ModelRouter(_policy(), client_factory=lambda provider, **kw: fake)
    role_client = router.for_role(LLMRole.ORCHESTRATOR)

    await role_client.close()

    assert fake.closed is False


# ---------------------------------------------------------------------------
# Role binding — model=None resolves to the role's model; explicit wins
# ---------------------------------------------------------------------------


async def test_model_none_resolves_to_role_model():
    fake = _FakeLLMClient()
    policy = _policy(models={"subagent": "ollama/qwen2.5:14b"})
    router = ModelRouter(policy, client_factory=lambda provider, **kw: fake)

    await router.for_role(LLMRole.SUBAGENT).complete([LLMMessage(role="user", content="hi")])

    assert fake.complete_calls[0]["model"] == "ollama/qwen2.5:14b"


async def test_explicit_model_passes_through_unchanged():
    """Per-agent config.json.llm_model overrides the role default."""
    fake = _FakeLLMClient()
    router = ModelRouter(_policy(), client_factory=lambda provider, **kw: fake)

    await router.for_role(LLMRole.SUBAGENT).complete(
        [LLMMessage(role="user", content="hi")], model="ollama/qwen2.5:32b"
    )

    assert fake.complete_calls[0]["model"] == "ollama/qwen2.5:32b"


async def test_stream_binds_model():
    fake = _FakeLLMClient()
    router = ModelRouter(_policy(), client_factory=lambda provider, **kw: fake)

    chunks = [
        c
        async for c in router.for_role(LLMRole.ORCHESTRATOR).stream(
            [LLMMessage(role="user", content="hi")]
        )
    ]

    assert fake.stream_calls[0]["model"] == "ollama/qwen2.5:7b"
    assert chunks[-1].is_final is True


async def test_count_tokens_binds_model():
    fake = _FakeLLMClient()
    router = ModelRouter(_policy(), client_factory=lambda provider, **kw: fake)

    await router.for_role(LLMRole.ORCHESTRATOR).count_tokens(
        [LLMMessage(role="user", content="hi")]
    )

    assert fake.count_tokens_calls[0]["model"] == "ollama/qwen2.5:7b"


async def test_tools_and_max_tokens_forwarded_verbatim():
    fake = _FakeLLMClient()
    router = ModelRouter(_policy(), client_factory=lambda provider, **kw: fake)
    sentinel_tools = [object()]

    await router.for_role(LLMRole.ORCHESTRATOR).complete(
        [LLMMessage(role="user", content="hi")], max_tokens=123, tools=sentinel_tools
    )

    assert fake.complete_calls[0]["max_tokens"] == 123
    assert fake.complete_calls[0]["tools"] is sentinel_tools


# ---------------------------------------------------------------------------
# Provider-prefix normalisation for direct (non-litellm) backends
# ---------------------------------------------------------------------------


def test_prefix_stripped_for_direct_anthropic_provider():
    policy = _policy(
        models={"orchestrator": "anthropic/claude-sonnet-4-6"},
        providers={"orchestrator": "anthropic"},
    )
    router = ModelRouter(policy)

    spec = router.spec_for(LLMRole.ORCHESTRATOR)

    assert spec.provider == "anthropic"
    assert spec.model == "claude-sonnet-4-6"


def test_mismatched_prefix_logs_warning(caplog):
    policy = _policy(
        models={"orchestrator": "ollama/qwen2.5:7b"},
        providers={"orchestrator": "anthropic"},
    )
    router = ModelRouter(policy)

    with caplog.at_level("WARNING"):
        spec = router.spec_for(LLMRole.ORCHESTRATOR)

    assert spec.provider == "anthropic"
    assert spec.model == "ollama/qwen2.5:7b"  # sent as-is; caller is warned
    assert any("carries a different provider" in r.message for r in caplog.records)


def test_no_prefix_model_unaffected_on_direct_provider():
    policy = _policy(models={"distill": "claude-haiku-4-5"}, providers={"distill": "anthropic"})
    router = ModelRouter(policy)

    spec = router.spec_for(LLMRole.DISTILL)

    assert spec.model == "claude-haiku-4-5"


def test_litellm_provider_never_strips_prefix():
    policy = _policy(
        models={"orchestrator": "ollama/qwen2.5:7b"}, providers={"orchestrator": "litellm"}
    )
    router = ModelRouter(policy)

    spec = router.spec_for(LLMRole.ORCHESTRATOR)

    assert spec.model == "ollama/qwen2.5:7b"


def test_describe_reports_all_six_roles():
    router = ModelRouter(_policy())
    described = router.describe()

    assert set(described.keys()) == {str(r) for r in LLMRole}
    assert described["orchestrator"] == {"provider": "litellm", "model": "ollama/qwen2.5:7b"}


# ---------------------------------------------------------------------------
# Ollama api_base metadata on ModelSpec
# ---------------------------------------------------------------------------


def test_spec_api_base_set_for_ollama_model(monkeypatch):
    # config.app is a cached_property returning a frozen AppConfig, so replace
    # the cached instance rather than mutate a field on it.
    monkeypatch.setattr(
        config, "app", dataclasses.replace(config.app, ollama_base_url="http://ollama.test:11434")
    )
    policy = _policy(models={"orchestrator": "ollama/qwen2.5:7b"})
    router = ModelRouter(policy)

    spec = router.spec_for(LLMRole.ORCHESTRATOR)

    assert spec.api_base == "http://ollama.test:11434"


def test_spec_api_base_none_for_hosted_model():
    policy = _policy(
        models={"orchestrator": "claude-sonnet-4-6"}, providers={"orchestrator": "anthropic"}
    )
    router = ModelRouter(policy)

    spec = router.spec_for(LLMRole.ORCHESTRATOR)

    assert spec.api_base is None
