"""Ollama LLM provider."""

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from nova.llm.base import LLMProvider
from nova.llm.models import (
    FunctionCall,
    Message,
    ModelInfo,
    StreamChunk,
    ToolCall,
)

DEFAULT_BASE_URL = "http://localhost:11434"
DEFAULT_MODEL = "qwen3:8b"


def _to_api_messages(messages: list[Message]) -> list[dict[str, Any]]:
    api_messages = []
    for m in messages:
        entry: dict[str, Any] = {"role": m.role.value, "content": m.content}
        if m.tool_calls:
            entry["tool_calls"] = [
                {
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    }
                }
                for tc in m.tool_calls
            ]
        api_messages.append(entry)
    return api_messages


class OllamaProvider(LLMProvider):
    """Ollama API provider."""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        default_model: str = DEFAULT_MODEL,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.default_model = default_model
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(timeout, connect=5.0),
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def is_available(self) -> bool:
        try:
            resp = await self._client.get("/api/tags")
            return resp.status_code == 200
        except httpx.HTTPError:
            return False

    async def list_models(self) -> list[ModelInfo]:
        resp = await self._client.get("/api/tags")
        resp.raise_for_status()
        data = resp.json()
        models = []
        for m in data.get("models", []):
            details = m.get("details", {})
            models.append(
                ModelInfo(
                    name=m["name"],
                    size=m.get("size", 0),
                    parameter_size=details.get("parameter_size", ""),
                    quantization=details.get("quantization_level", ""),
                    context_length=details.get("context_length", 0),
                    capabilities=details.get("capabilities", []),
                )
            )
        return models

    async def chat(
        self,
        messages: list[Message],
        model: str = "",
        tools: list[dict[str, Any]] | None = None,
        options: dict[str, Any] | None = None,
    ) -> str:
        model = model or self.default_model
        payload: dict[str, Any] = {
            "model": model,
            "messages": _to_api_messages(messages),
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
        if options:
            payload["options"] = options
        resp = await self._client.post("/api/chat", json=payload)
        resp.raise_for_status()
        data = resp.json()
        return data["message"]["content"]

    async def chat_stream(
        self,
        messages: list[Message],
        model: str = "",
        tools: list[dict[str, Any]] | None = None,
        options: dict[str, Any] | None = None,
    ) -> AsyncIterator[StreamChunk]:
        model = model or self.default_model
        payload: dict[str, Any] = {
            "model": model,
            "messages": _to_api_messages(messages),
            "stream": True,
        }
        if tools:
            payload["tools"] = tools
        if options:
            payload["options"] = options
        async with self._client.stream("POST", "/api/chat", json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line:
                    continue
                chunk = json.loads(line)
                message = chunk.get("message", {})
                done = chunk.get("done", False)
                tool_calls = [
                    ToolCall(
                        function=FunctionCall(
                            name=tc["function"]["name"],
                            arguments=tc["function"].get("arguments", {}),
                        )
                    )
                    for tc in message.get("tool_calls", [])
                ]
                yield StreamChunk(
                    token=message.get("content", "") if not done else "",
                    done=done,
                    tool_calls=tool_calls,
                    total_duration_ns=chunk.get("total_duration", 0),
                    eval_count=chunk.get("eval_count", 0),
                    eval_duration_ns=chunk.get("eval_duration", 0),
                    prompt_eval_count=chunk.get("prompt_eval_count", 0),
                    prompt_eval_duration_ns=chunk.get("prompt_eval_duration", 0),
                )