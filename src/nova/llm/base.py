"""Abstract LLM provider interface."""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from nova.llm.models import Message, ModelInfo, StreamChunk


class LLMProvider(ABC):
    """Base class for LLM providers.

    All LLM communication goes through this interface.
    The agent core never talks to a specific provider directly.
    """

    @abstractmethod
    async def is_available(self) -> bool:
        """Check if the provider is reachable."""

    @abstractmethod
    async def list_models(self) -> list[ModelInfo]:
        """List available models."""

    async def tool_mode(self, model: str) -> str:
        """How a model handles tool calling.

        Returns "native" (structured tool calls), "prompt" (text fallback
        required), or "unsupported". Providers that do not override this
        default to native structured tool calling.
        """
        return "native"

    @abstractmethod
    async def chat(self, messages: list[Message], model: str) -> str:
        """Send messages and return the full response."""

    @abstractmethod
    async def chat_stream(
        self, messages: list[Message], model: str
    ) -> AsyncIterator[StreamChunk]:
        """Send messages and yield response tokens as they arrive."""
        yield  # pragma: no cover
