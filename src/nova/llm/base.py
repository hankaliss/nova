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

    @abstractmethod
    async def chat(self, messages: list[Message], model: str) -> str:
        """Send messages and return the full response."""

    @abstractmethod
    async def chat_stream(
        self, messages: list[Message], model: str
    ) -> AsyncIterator[StreamChunk]:
        """Send messages and yield response tokens as they arrive."""
        yield  # pragma: no cover
