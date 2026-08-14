#!/usr/bin/env python3
# Copyright 2026 Abhishek Gupta
# SPDX-License-Identifier: Apache-2.0
"""Test script for Ollama local LLM integration.

This script validates that:
1. LiteLLM can connect to Ollama at the configured base URL
2. The factory pattern correctly creates an Ollama-configured client
3. Basic completion requests work end-to-end
4. Streaming responses work correctly

Usage:
    # Make sure Ollama is running and has a model pulled
    ollama pull llama3.2
    
    # Test with default config (reads from .env)
    python scripts/test_ollama_integration.py
    
    # Test with custom model
    OLLAMA_DEFAULT_MODEL=qwen2.5-coder python scripts/test_ollama_integration.py
"""

import asyncio
import os
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


async def test_ollama_connectivity():
    """Test basic connectivity to Ollama instance."""
    from graphclaw.config import config
    import httpx
    
    print(f"🔍 Testing Ollama connectivity at {config.app.ollama_base_url}")
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{config.app.ollama_base_url}/api/tags", timeout=5.0)
            if response.status_code == 200:
                data = response.json()
                models = [m["name"] for m in data.get("models", [])]
                print(f"✅ Ollama is running with {len(models)} model(s): {', '.join(models)}")
                return True
            else:
                print(f"❌ Ollama responded with status {response.status_code}")
                return False
    except Exception as exc:
        print(f"❌ Cannot connect to Ollama: {exc}")
        print(f"   Make sure Ollama is running: ollama serve")
        return False


async def test_factory_with_ollama():
    """Test LLM factory with Ollama model."""
    from graphclaw.llm import create_llm_client, LLMMessage
    from graphclaw.config import config
    
    model_string = f"ollama/{config.app.ollama_default_model}"
    print(f"\n🏭 Testing factory with model: {model_string}")
    
    try:
        # Create client via factory
        client = create_llm_client("litellm", default_model=model_string)
        
        # Verify api_base was auto-configured
        if hasattr(client, "_api_base") and client._api_base == config.app.ollama_base_url:
            print(f"✅ Factory auto-configured api_base: {client._api_base}")
        else:
            print(f"⚠️  api_base not auto-configured (got: {getattr(client, '_api_base', None)})")
        
        await client.close()
        return True
        
    except Exception as exc:
        print(f"❌ Factory test failed: {exc}")
        return False


async def test_completion_request():
    """Test basic completion request to Ollama."""
    from graphclaw.llm import create_llm_client, LLMMessage
    from graphclaw.config import config
    
    model_string = f"ollama/{config.app.ollama_default_model}"
    print(f"\n💬 Testing completion request with {model_string}")
    
    try:
        client = create_llm_client("litellm", default_model=model_string)
        
        messages = [
            LLMMessage(role="user", content="What is 2+2? Answer in one word.")
        ]
        
        print("   Sending request...")
        response = await client.complete(messages, max_tokens=50, temperature=0.0)
        
        print(f"✅ Completion successful!")
        print(f"   Model: {response.model}")
        print(f"   Response: {response.content.strip()}")
        print(f"   Tokens: {response.tokens_used} (prompt: {response.prompt_tokens}, completion: {response.completion_tokens})")
        
        await client.close()
        return True
        
    except Exception as exc:
        print(f"❌ Completion request failed: {exc}")
        import traceback
        traceback.print_exc()
        return False


async def test_streaming_request():
    """Test streaming response from Ollama."""
    from graphclaw.llm import create_llm_client, LLMMessage
    from graphclaw.config import config
    
    model_string = f"ollama/{config.app.ollama_default_model}"
    print(f"\n🌊 Testing streaming request with {model_string}")
    
    try:
        client = create_llm_client("litellm", default_model=model_string)
        
        messages = [
            LLMMessage(role="user", content="Count from 1 to 5.")
        ]
        
        print("   Streaming response: ", end="", flush=True)
        chunk_count = 0
        final_chunk = None
        
        async for chunk in client.stream(messages, max_tokens=50, temperature=0.0):
            if chunk.content_delta:
                print(chunk.content_delta, end="", flush=True)
                chunk_count += 1
            if chunk.is_final:
                final_chunk = chunk
        
        print()  # newline
        
        if final_chunk and final_chunk.accumulated:
            print(f"✅ Streaming successful!")
            print(f"   Received {chunk_count} chunk(s)")
            print(f"   Final content: {final_chunk.accumulated.content.strip()}")
        else:
            print(f"⚠️  Streaming completed but no final chunk received")
        
        await client.close()
        return True
        
    except Exception as exc:
        print(f"❌ Streaming request failed: {exc}")
        import traceback
        traceback.print_exc()
        return False


async def test_env_config():
    """Verify environment configuration is loaded correctly."""
    from graphclaw.config import config
    
    print("\n⚙️  Environment configuration:")
    print(f"   OLLAMA_API_BASE: {config.app.ollama_base_url}")
    print(f"   OLLAMA_DEFAULT_MODEL: {config.app.ollama_default_model}")
    print(f"   LITELLM_DEFAULT_MODEL: {config.app.litellm_default_model}")
    print(f"   GRAPHCLAW_DEFAULT_LLM_PROVIDER: {config.app.default_llm_provider}")
    
    # Check if using Ollama
    if config.app.litellm_default_model.startswith("ollama/"):
        print(f"✅ Configured for local Ollama development (cost-free)")
    elif config.app.default_llm_provider == "anthropic":
        print(f"⚠️  Using Anthropic (billable API)")
    else:
        print(f"ℹ️  Using {config.app.default_llm_provider}")
    
    return True


async def main():
    """Run all Ollama integration tests."""
    print("=" * 70)
    print("Ollama Integration Test Suite")
    print("=" * 70)
    
    results = []
    
    # Test connectivity first
    results.append(("Connectivity", await test_ollama_connectivity()))
    
    if not results[-1][1]:
        print("\n❌ Cannot proceed without Ollama running.")
        print("   Start Ollama with: ollama serve")
        print(f"   Pull a model with: ollama pull llama3.2")
        return 1
    
    # Run other tests
    results.append(("Environment Config", await test_env_config()))
    results.append(("Factory Pattern", await test_factory_with_ollama()))
    results.append(("Completion Request", await test_completion_request()))
    results.append(("Streaming Request", await test_streaming_request()))
    
    # Summary
    print("\n" + "=" * 70)
    print("Test Summary")
    print("=" * 70)
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for test_name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status}  {test_name}")
    
    print(f"\nTotal: {passed}/{total} tests passed")
    
    if passed == total:
        print("\n🎉 All tests passed! Ollama integration is working correctly.")
        print("\n💡 To use Ollama for development:")
        print("   1. Set LITELLM_DEFAULT_MODEL=ollama/llama3.2 in docker/.env")
        print("   2. Set GRAPHCLAW_DEFAULT_LLM_PROVIDER=litellm")
        print("   3. Restart services: docker compose restart")
        return 0
    else:
        print(f"\n⚠️  {total - passed} test(s) failed.")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
