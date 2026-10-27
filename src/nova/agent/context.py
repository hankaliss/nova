"""Agent core for Nova."""

import json
import math

from nova.llm.models import Message, Role, ToolCall

# Rough chars-per-token ratio. Deliberately low (real English is ~4) because
# code, JSON and tool output tokenize densely; overestimating is the safe side.
_CHARS_PER_TOKEN = 3
# Per-message framing overhead (role markers, separators) in the chat template.
_MESSAGE_OVERHEAD_TOKENS = 4


def estimate_tokens(text: str, chars_per_token: float = _CHARS_PER_TOKEN) -> int:
    """Cheap token estimate for budgeting. The default ratio is deliberately
    low (overestimates tokens); the Agent passes a measured one (AD-038)."""
    return math.ceil(len(text) / chars_per_token)


def estimate_message_tokens(
    message: Message, chars_per_token: float = _CHARS_PER_TOKEN
) -> int:
    tokens = _MESSAGE_OVERHEAD_TOKENS + estimate_tokens(message.content, chars_per_token)
    if message.tool_calls:
        tokens += estimate_tokens(
            json.dumps([c.model_dump() for c in message.tool_calls]), chars_per_token
        )
    return tokens


def truncate_middle(text: str, max_chars: int) -> str:
    """Keep the head and tail of ``text`` within ``max_chars``.

    Tool output usually has its useful parts at both ends (headers, final
    lines, error messages), so the middle is what gets dropped.
    """
    if len(text) <= max_chars:
        return text
    marker = "\n[... {} characters truncated ...]\n"
    keep = max(0, max_chars - len(marker.format(len(text))))
    head = keep * 2 // 3
    tail = keep - head
    dropped = len(text) - head - tail
    return text[:head] + marker.format(dropped) + (text[-tail:] if tail else "")

DEFAULT_SYSTEM_PROMPT = (
    "You are Nova, a helpful local AI desktop assistant. "
    "Be concise, accurate, and helpful. "
    "Use tools when you need real system data — do not guess. "
    "If a tool is unavailable for a task, tell the user instead of making something up.\n"
    "\n"
    "When asked to create web pages, interactive components, games, visualizations, "
    "or UI demos, provide a complete, self-contained single-file HTML document "
    "(with inline <style> and <script>) inside a ```html code block so Nova can render it directly."
)


class ConversationContext:
    """Manages conversation message history and system prompt."""

    def __init__(
        self,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
        max_messages: int = 100,
    ) -> None:
        self.system_prompt = system_prompt
        # Remembered facts relevant to the current turn, set per message by
        # the RPC layer; sent as part of the system message.
        self.memory_note = ""
        self.max_messages = max_messages
        self._messages: list[Message] = []

    @property
    def messages(self) -> list[Message]:
        return list(self._messages)

    def add_user(self, content: str) -> None:
        self._messages.append(Message(role=Role.USER, content=content))
        self._trim()

    def add_assistant(self, content: str) -> None:
        self._messages.append(Message(role=Role.ASSISTANT, content=content))
        self._trim()

    def add_assistant_tool_call(
        self, content: str, tool_calls: list[ToolCall]
    ) -> None:
        """Record an assistant turn that requested tools (may have no text)."""
        self._messages.append(
            Message(role=Role.ASSISTANT, content=content, tool_calls=tool_calls)
        )
        self._trim()

    def add_tool_result(self, content: str) -> None:
        self._messages.append(Message(role=Role.TOOL, content=content))
        self._trim()

    def to_messages(
        self, max_tokens: int | None = None, chars_per_token: float = _CHARS_PER_TOKEN
    ) -> list[Message]:
        """System prompt + history, newest-first fitted to ``max_tokens``.

        With a budget, older messages are left out (not deleted) so the
        request fits the model's context window instead of Ollama silently
        cutting the start of the prompt — which would drop the system prompt.
        The current turn (from the last user message on) is always kept, and
        the window always starts at a user message so tool results are never
        sent without the call that produced them.
        """
        content = self.system_prompt
        if self.memory_note:
            content = f"{content}\n\n{self.memory_note}"
        system = Message(role=Role.SYSTEM, content=content)
        if max_tokens is None:
            return [system, *self._messages]

        def cost(message: Message) -> int:
            return estimate_message_tokens(message, chars_per_token)

        start = self._current_turn_start()
        remaining = max_tokens - cost(system)
        remaining -= sum(cost(m) for m in self._messages[start:])
        candidate = start
        for i in range(start - 1, -1, -1):
            remaining -= cost(self._messages[i])
            if remaining < 0:
                break
            if self._messages[i].role == Role.USER:
                candidate = i
        return [system, *self._messages[candidate:]]

    def drop_last_turn(self) -> str | None:
        """Remove the last user message and everything after it (tool calls,
        tool results, answers). Returns that user message's text."""
        for i in range(len(self._messages) - 1, -1, -1):
            if self._messages[i].role == Role.USER:
                text = self._messages[i].content
                del self._messages[i:]
                return text
        return None

    def _current_turn_start(self) -> int:
        for i in range(len(self._messages) - 1, -1, -1):
            if self._messages[i].role == Role.USER:
                return i
        return 0

    def clear(self) -> None:
        self._messages.clear()

    def _trim(self) -> None:
        while len(self._messages) > self.max_messages:
            self._messages.pop(0)
