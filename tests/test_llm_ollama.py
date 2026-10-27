"""Tests for Ollama provider (mocked HTTP)."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from nova.llm.models import Message, ModelInfo, Role
from nova.llm.ollama import OllamaProvider


def _provider_with_models(models: list[ModelInfo]) -> OllamaProvider:
    provider = OllamaProvider(
        base_url="http://localhost:11434", default_model="qwen3:8b"
    )
    provider._model_info = models
    provider._model_info_at = 999999.0  # treat cache as fresh
    return provider


@pytest.fixture
def provider():
    return OllamaProvider(base_url="http://localhost:11434", default_model="qwen3:8b")


@pytest.mark.asyncio
async def test_is_available_success(provider):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    provider._client.get = AsyncMock(return_value=mock_resp)

    assert await provider.is_available() is True


@pytest.mark.asyncio
async def test_is_available_failure(provider):
    import httpx

    provider._client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))

    assert await provider.is_available() is False


@pytest.mark.asyncio
async def test_list_models(provider):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "models": [
            {
                "name": "qwen3:8b",
                "size": 5225388164,
                "details": {
                    "parameter_size": "8.2B",
                    "quantization_level": "Q4_K_M",
                    "context_length": 40960,
                    "capabilities": ["completion", "tools", "thinking"],
                },
            }
        ]
    }
    provider._client.get = AsyncMock(return_value=mock_resp)

    models = await provider.list_models()
    assert len(models) == 1
    assert models[0].name == "qwen3:8b"
    assert models[0].parameter_size == "8.2B"
    assert models[0].context_length == 40960


@pytest.mark.asyncio
async def test_chat(provider):
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "message": {"role": "assistant", "content": "Hello! How can I help?"}
    }
    provider._client.post = AsyncMock(return_value=mock_resp)

    messages = [Message(role=Role.USER, content="Hi")]
    result = await provider.chat(messages)
    assert result == "Hello! How can I help?"


@pytest.mark.asyncio
async def test_chat_uses_default_model(provider):
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"message": {"role": "assistant", "content": "ok"}}
    provider._client.post = AsyncMock(return_value=mock_resp)

    messages = [Message(role=Role.USER, content="test")]
    await provider.chat(messages)

    call_args = provider._client.post.call_args
    assert call_args[1]["json"]["model"] == "qwen3:8b"


@pytest.mark.asyncio
async def test_chat_custom_model(provider):
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"message": {"role": "assistant", "content": "ok"}}
    provider._client.post = AsyncMock(return_value=mock_resp)

    messages = [Message(role=Role.USER, content="test")]
    await provider.chat(messages, model="other-model")

    call_args = provider._client.post.call_args
    assert call_args[1]["json"]["model"] == "other-model"


@pytest.mark.asyncio
async def test_chat_stream(provider):
    chunks = [
        json.dumps({"message": {"content": "Hello"}, "done": False}),
        json.dumps({"message": {"content": ""}, "done": True, "eval_count": 5}),
    ]

    async def mock_aiter_lines():
        for c in chunks:
            yield c

    mock_stream_ctx = AsyncMock()
    mock_stream_ctx.__aenter__ = AsyncMock(return_value=mock_stream_ctx)
    mock_stream_ctx.__aexit__ = AsyncMock(return_value=False)
    mock_stream_ctx.aiter_lines = mock_aiter_lines
    mock_stream_ctx.raise_for_status = MagicMock()

    provider._client.stream = MagicMock(return_value=mock_stream_ctx)

    messages = [Message(role=Role.USER, content="Hi")]
    results = []
    async for chunk in provider.chat_stream(messages):
        results.append(chunk)

    assert len(results) == 2
    assert results[0].token == "Hello"
    assert results[0].done is False
    assert results[1].done is True
    assert results[1].eval_count == 5


@pytest.mark.asyncio
async def test_close(provider):
    provider._client.aclose = AsyncMock()
    await provider.close()
    provider._client.aclose.assert_called_once()


@pytest.mark.asyncio
async def test_tool_mode_native_family():
    provider = _provider_with_models(
        [
            ModelInfo(
                name="qwen3:8b",
                capabilities=["completion", "tools", "thinking"],
            )
        ]
    )
    assert await provider.tool_mode("qwen3:8b") == "native"


@pytest.mark.asyncio
async def test_tool_mode_coder_falls_back_to_prompt():
    provider = _provider_with_models(
        [
            ModelInfo(
                name="qwen2.5-coder:7b",
                capabilities=["completion", "tools", "insert"],
            )
        ]
    )
    assert await provider.tool_mode("qwen2.5-coder:7b") == "prompt"


@pytest.mark.asyncio
async def test_tool_mode_no_tools_capability_unsupported():
    provider = _provider_with_models(
        [ModelInfo(name="llama2:7b", capabilities=["completion"])]
    )
    assert await provider.tool_mode("llama2:7b") == "unsupported"


@pytest.mark.asyncio
async def test_tool_mode_claims_tools_but_unverified_prompt():
    provider = _provider_with_models(
        [ModelInfo(name="gemma2:9b", capabilities=["completion", "tools"])]
    )
    assert await provider.tool_mode("gemma2:9b") == "prompt"


@pytest.mark.asyncio
async def test_tool_mode_empty_uses_default_model():
    provider = _provider_with_models(
        [ModelInfo(name="qwen3:8b", capabilities=["tools"])]
    )
    assert await provider.tool_mode("") == "native"


@pytest.mark.asyncio
async def test_tool_mode_offline_falls_back_to_family():
    import httpx

    provider = OllamaProvider(base_url="http://localhost:11434")
    provider._client.get = AsyncMock(side_effect=httpx.ConnectError("offline"))
    assert await provider.tool_mode("qwen3:8b") == "native"
    assert await provider.tool_mode("qwen2.5-coder:7b") == "prompt"
    assert await provider.tool_mode("llama2:7b") == "unsupported"


@pytest.mark.asyncio
async def test_tool_mode_cache_hits():
    provider = _provider_with_models(
        [ModelInfo(name="qwen3:8b", capabilities=["tools"])]
    )
    assert await provider.tool_mode("qwen3:8b") == "native"
    provider._client.get = AsyncMock(side_effect=AssertionError("should be cached"))
    assert await provider.tool_mode("qwen3:8b") == "native"


@pytest.mark.asyncio
async def test_chat_passes_options(provider):
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"message": {"role": "assistant", "content": "ok"}}
    provider._client.post = AsyncMock(return_value=mock_resp)

    messages = [Message(role=Role.USER, content="test")]
    await provider.chat(messages, options={"temperature": 0.3, "num_ctx": 8192})

    payload = provider._client.post.call_args[1]["json"]
    assert payload["options"] == {"temperature": 0.3, "num_ctx": 8192}


@pytest.mark.asyncio
async def test_chat_stream_passes_options(provider):
    chunks = [
        json.dumps({"message": {"content": "Hello"}, "done": False}),
        json.dumps({"message": {"content": ""}, "done": True}),
    ]

    async def mock_aiter_lines():
        for c in chunks:
            yield c

    mock_stream_ctx = AsyncMock()
    mock_stream_ctx.__aenter__ = AsyncMock(return_value=mock_stream_ctx)
    mock_stream_ctx.__aexit__ = AsyncMock(return_value=False)
    mock_stream_ctx.aiter_lines = mock_aiter_lines
    mock_stream_ctx.raise_for_status = MagicMock()

    provider._client.stream = MagicMock(return_value=mock_stream_ctx)

    messages = [Message(role=Role.USER, content="test")]
    async for _ in provider.chat_stream(messages, options={"temperature": 0.5}):
        pass

    payload = provider._client.stream.call_args[1]["json"]
    assert payload["options"] == {"temperature": 0.5}
