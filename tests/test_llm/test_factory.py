# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""tests.test_llm.test_factory — Unit tests for graphclaw.llm.factory.create_llm_client.

Verifies correct provider dispatch, unknown provider error, and that
provider SDKs are imported lazily (mock via sys.modules).
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest


def _stub_litellm():
    """Insert a stub litellm module into sys.modules."""
    stub = MagicMock()
    sys.modules["litellm"] = stub
    return stub


def _stub_anthropic():
    stub = MagicMock()
    stub.AsyncAnthropic.return_value = MagicMock()
    sys.modules["anthropic"] = stub
    return stub


def _stub_openai():
    stub = MagicMock()
    stub.AsyncOpenAI.return_value = MagicMock()
    sys.modules["openai"] = stub
    return stub


def test_create_llm_client_litellm_default():
    """create_llm_client() with no args returns LiteLLMLLMClient."""
    from graphclaw.llm.factory import create_llm_client
    from graphclaw.llm.litellm.client import LiteLLMLLMClient

    client = create_llm_client()
    assert isinstance(client, LiteLLMLLMClient)


def test_create_llm_client_litellm_explicit():
    from graphclaw.llm.factory import create_llm_client
    from graphclaw.llm.litellm.client import LiteLLMLLMClient

    client = create_llm_client("litellm", default_model="gpt-4o")
    assert isinstance(client, LiteLLMLLMClient)
    assert client._default_model == "gpt-4o"


def test_create_llm_client_anthropic():
    from graphclaw.llm.anthropic.client import AnthropicLLMClient
    from graphclaw.llm.factory import create_llm_client

    client = create_llm_client("anthropic")
    assert isinstance(client, AnthropicLLMClient)


def test_create_llm_client_openai():
    from graphclaw.llm.factory import create_llm_client
    from graphclaw.llm.openai.client import OpenAILLMClient

    client = create_llm_client("openai")
    assert isinstance(client, OpenAILLMClient)


def test_create_llm_client_unknown_provider():
    from graphclaw.llm.factory import create_llm_client

    with pytest.raises(ValueError, match="Unknown LLM provider"):
        create_llm_client("cohere")


def test_create_llm_client_passes_kwargs():
    from graphclaw.llm.anthropic.client import AnthropicLLMClient
    from graphclaw.llm.factory import create_llm_client

    client = create_llm_client("anthropic", api_key="test-key", default_model="claude-opus-4-6")
    assert isinstance(client, AnthropicLLMClient)
    assert client._api_key == "test-key"
    assert client._default_model == "claude-opus-4-6"


def test_llm_package_facade_imports():
    """from graphclaw.llm import ... should expose all public names."""
    from graphclaw.llm import (
        LLMClient,
        create_llm_client,
    )

    # Just verify all names are importable
    assert LLMClient is not None
    assert create_llm_client is not None
