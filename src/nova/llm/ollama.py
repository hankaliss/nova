"""Ollama LLM provider."""

import json
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx

from nova.llm.base import LLMProvider
from nova.llm.context_fit import kv_bytes_per_token
from nova.llm.models import (
    FunctionCall,
    Message,
    ModelInfo,
    StreamChunk,
    ToolCall,
)

DEFAULT_BASE_URL = "http://localhost:11434"
DEFAULT_MODEL = "qwen3:4b"

# Model families verified to emit structured Ollama tool_calls on /api/chat.
NATIVE_TOOL_FAMILIES = (
    "qwen3",
    "llama3.1",
    "llama3.2",
    "llama3.3",
    "mistral",
    "gemma3",
    "phi4",
    "smollm2",
    "minicpm3",
)

# Model families known to emit tool calls as raw JSON text even when their
# template advertises a "tools" capability (e.g. code-focused models).
TEXT_TOOL_FAMILIES = ("coder", "codegemma", "starcoder")


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
        self._model_info: list[ModelInfo] = []
        self._model_info_at = 0.0
        self._info_ttl_s = 60.0
        self._mode_cache: dict[str, tuple[float, str]] = {}

    async def close(self) -> None:
        await self._client.aclose()

    async def _model_infos(self) -> list[ModelInfo]:
        now = time.monotonic()
        if not self._model_info or now - self._model_info_at > self._info_ttl_s:
            self._model_info = await self.list_models()
            self._model_info_at = now
        return self._model_info

    @staticmethod
    def _family_tool_mode(model: str) -> str:
        name = model.lower()
        if any(f in name for f in TEXT_TOOL_FAMILIES):
            return "prompt"
        if any(f in name for f in NATIVE_TOOL_FAMILIES):
            return "native"
        return "unsupported"

    def _capabilities_for(self, model: str) -> list[str]:
        for info in self._model_info:
            if info.name == model:
                return info.capabilities
        return []

    async def tool_mode(self, model: str) -> str:
        """Return the tool-calling strategy for a model.

        Returns one of:
        - "native": verified to emit structured Ollama tool_calls
        - "prompt": template advertises tools but unverified — fall back to
          text-based tool calling (schemas still sent to the model)
        - "unsupported": no tool support — text-based tool calling only
        """
        if not model:
            model = self.default_model
        cached = self._mode_cache.get(model)
        if cached and time.monotonic() - cached[0] < 300.0:
            return cached[1]

        default = self._family_tool_mode(model)
        mode = default
        try:
            await self._model_infos()
            capabilities = self._capabilities_for(model)
        except httpx.HTTPError:
            capabilities = []
        if capabilities:
            if "tools" not in capabilities:
                mode = "unsupported"
            elif default != "native":
                mode = "prompt"
        self._mode_cache[model] = (time.monotonic(), mode)
        return mode

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
                    capabilities=m.get("capabilities", []),
                )
            )
        return models

    # ---- Context fitting (see nova.llm.context_fit) ---------------------

    async def model_profile(self, model: str) -> dict[str, Any]:
        """File size, KV-cache bytes per token and trained context of ``model``."""
        resp = await self._client.post("/api/show", json={"model": model})
        resp.raise_for_status()
        model_info = resp.json().get("model_info", {}) or {}
        arch = model_info.get("general.architecture", "")
        size = next((m.size for m in await self._model_infos() if m.name == model), 0)
        return {
            "size": size,
            "kv_bytes_per_token": kv_bytes_per_token(model_info),
            "context_length": model_info.get(f"{arch}.context_length"),
        }

    async def load(self, model: str, num_ctx: int) -> tuple[int, int]:
        """Load ``model`` at ``num_ctx`` (no generation); return (size, size_vram).

        Fully on the GPU when ``size_vram == size``. A different ``num_ctx``
        than the loaded one makes Ollama reload the model.
        """
        resp = await self._client.post(
            "/api/generate",
            json={"model": model, "prompt": "", "stream": False,
                  "options": {"num_ctx": num_ctx}},
            timeout=httpx.Timeout(300.0, connect=5.0),
        )
        resp.raise_for_status()
        return await self.placement(model) or (0, 0)

    async def placement(self, model: str) -> tuple[int, int] | None:
        """(size, size_vram) of ``model`` if Ollama has it loaded."""
        resp = await self._client.get("/api/ps")
        resp.raise_for_status()
        for m in resp.json().get("models", []):
            if m.get("name") == model or m.get("model") == model:
                return int(m.get("size", 0)), int(m.get("size_vram", 0))
        return None

    async def loaded_vram_mib(self) -> int:
        """GPU memory held by models Ollama has loaded (all of them)."""
        resp = await self._client.get("/api/ps")
        resp.raise_for_status()
        return sum(int(m.get("size_vram", 0)) for m in resp.json().get("models", [])) // 2**20

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