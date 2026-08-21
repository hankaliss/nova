"""Agent for Nova — orchestrates LLM conversations."""

import json
import time
from collections.abc import AsyncIterator, Callable
from typing import Any

from nova.agent.context import DEFAULT_SYSTEM_PROMPT, ConversationContext
from nova.llm.base import LLMProvider
from nova.llm.models import Message, Role, StreamChunk, ToolCall
from nova.tools.registry import ToolRegistry


class Agent:
    """Orchestrates conversation between user and LLM.

    Manages context, handles streaming, executes tools, and provides
    the interface the UI calls.
    """

    def __init__(
        self,
        provider: LLMProvider,
        model: str = "",
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
        max_messages: int = 100,
        registry: ToolRegistry | None = None,
        max_tool_iterations: int = 10,
        options: dict[str, Any] | None = None,
    ) -> None:
        self.provider = provider
        self.model = model
        self.registry = registry
        self.max_tool_iterations = max_tool_iterations
        self.options = dict(options or {})
        self.context = ConversationContext(
            system_prompt=system_prompt,
            max_messages=max_messages,
        )
        self.tool_callback: Callable[[dict[str, Any]], Any] | None = None

    async def _notify(self, event: dict[str, Any]) -> None:
        """Fire the optional tool-event callback (UI instrumentation)."""
        if self.tool_callback is None:
            return
        result = self.tool_callback(event)
        if hasattr(result, "__await__"):
            await result

    def _tool_schemas(self) -> list[dict] | None:
        if self.registry is None:
            return None
        return self.registry.to_schemas()

    async def _generate(self) -> AsyncIterator[StreamChunk]:
        """Generate a response, handling tool calls.

        Context is updated with assistant and tool messages as needed.
        """
        for _ in range(self.max_tool_iterations):
            full_response = ""
            tool_calls: list[ToolCall] = []

            async for chunk in self.provider.chat_stream(
                self.context.to_messages(),
                model=self.model,
                tools=self._tool_schemas(),
                options=self.options or None,
            ):
                if chunk.tool_calls:
                    tool_calls.extend(chunk.tool_calls)
                    yield chunk.model_copy(update={"token": ""})
                    continue
                if chunk.token:
                    full_response += chunk.token
                yield chunk

            if not tool_calls:
                self.context.add_assistant(full_response)
                return

            self.context._messages.append(
                Message(role=Role.ASSISTANT, content=full_response, tool_calls=tool_calls)
            )
            for call in tool_calls:
                name = call.function.name
                args = dict(call.function.arguments)
                await self._notify({"type": "start", "name": name, "args": args})
                started = time.monotonic()
                result = await self.registry.execute(name, args)
                duration_ms = int((time.monotonic() - started) * 1000)
                await self._notify(
                    {
                        "type": "end",
                        "name": name,
                        "ok": result.success,
                        "duration_ms": duration_ms,
                        "error": "" if result.success else result.error,
                    }
                )
                content = json.dumps(result.model_dump()) if result.success else result.error
                self.context._messages.append(
                    Message(role=Role.TOOL, content=content)
                )

        error = "Tool call limit exceeded — stopping after "
        error += f"{self.max_tool_iterations} iterations."
        self.context.add_assistant(error)
        yield StreamChunk(token=error, done=True)

    async def send(self, content: str) -> AsyncIterator[StreamChunk]:
        """Send a user message and yield response tokens as they arrive."""
        self.context.add_user(content)
        async for chunk in self._generate():
            yield chunk

    async def send_sync(self, content: str) -> str:
        """Send a user message and return the full response."""
        full = ""
        async for chunk in self.send(content):
            if chunk.token:
                full += chunk.token
        return full

    def reset(self) -> None:
        """Clear conversation history."""
        self.context.clear()