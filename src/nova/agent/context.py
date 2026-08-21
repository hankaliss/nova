"""Agent core for Nova."""

from nova.llm.models import Message, Role

DEFAULT_SYSTEM_PROMPT = (
    "You are Nova, a helpful local AI desktop assistant. "
    "You run on the user's CachyOS machine with KDE Plasma. "
    "Be concise, accurate, and helpful. "
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

    def pop_last_assistant(self) -> Message | None:
        """Remove and return the last assistant message, if any."""
        for i in range(len(self._messages) - 1, -1, -1):
            if self._messages[i].role == Role.ASSISTANT:
                return self._messages.pop(i)
        return None

    def pop_last_user(self) -> Message | None:
        """Remove and return the last user message, if any."""
        for i in range(len(self._messages) - 1, -1, -1):
            if self._messages[i].role == Role.USER:
                return self._messages.pop(i)
        return None

    def to_messages(self) -> list[Message]:
        result = [Message(role=Role.SYSTEM, content=self.system_prompt)]
        result.extend(self._messages)
        return result

    def clear(self) -> None:
        self._messages.clear()

    def _trim(self) -> None:
        while len(self._messages) > self.max_messages:
            self._messages.pop(0)
