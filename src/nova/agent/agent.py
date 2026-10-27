"""Agent for Nova — orchestrates LLM conversations."""

import json
import re
import time
from collections.abc import AsyncIterator, Callable
from typing import Any

from nova.agent.context import (
    DEFAULT_SYSTEM_PROMPT,
    ConversationContext,
    estimate_tokens,
    truncate_middle,
)
from nova.llm.base import LLMProvider
from nova.llm.models import FunctionCall, Message, Role, StreamChunk, ToolCall
from nova.tools.registry import ToolRegistry
from nova.tools.report import ARTIFACT_KEY, artifact_block

_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*\n([\s\S]*?)\n?```", re.MULTILINE)
_JSON_KEY_RE = re.compile(r'"(?:tool|name)"\s*:')

# Ollama's context size when the request sets no num_ctx.
DEFAULT_NUM_CTX = 4096

# Measured characters per token (AD-038): clamped, smoothed, and used with a
# margin so an estimate errs towards more tokens.
_CPT_RANGE = (2.0, 6.0)
_CPT_MARGIN = 0.9

# Fence languages a text-mode tool call may use; other fences stream as-is.
_CALL_FENCE_LANGS = {"", "json", "tool", "tool_call", "tool_code"}
# A held block longer than this is released as text (never a tool call).
_MAX_HELD = 8000


def _object_end(text: str) -> int | None:
    """Index after the JSON object starting at text[0], or None if unclosed."""
    depth = 0
    in_string = escaped = False
    for i, ch in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i + 1
    return None


class _CallHider:
    """Text mode: keeps JSON tool calls out of the visible, saved reply.

    Text-mode models (AD-018) write calls as JSON. The model's context keeps
    them, but the user saw them stream by and they stayed in the transcript.
    This holds back a block only while it could be a call (a json/plain
    fence, or a "{" at the start of a line), drops it once complete if it
    names a registered tool, and releases everything else unchanged.
    """

    def __init__(self, tool_names: set[str]) -> None:
        self._tools = tool_names
        self._pending = ""
        self._line_start = True  # pending[0] begins a line

    def feed(self, token: str) -> str:
        self._pending += token
        return self._drain(final=False)

    def finish(self) -> str:
        return self._drain(final=True)

    def _is_call(self, block: str) -> bool:
        calls = Agent._parse_tool_calls(block)
        return bool(calls) and all(c.function.name in self._tools for c in calls)

    def _drain(self, final: bool) -> str:
        out = ""
        while self._pending:
            text = self._pending
            start = self._find_start(text)
            if start is None:
                # Keep a possible beginning of a fence ("`", "``") or a line
                # that may still start with "{" (indent only) for later.
                keep = 0 if final else self._unsafe_tail(text)
                out += self._emit(text[: len(text) - keep])
                self._pending = text[len(text) - keep:]
                return out
            out += self._emit(text[:start])
            block = text[start:]
            end, is_fence = self._block_end(block, final)
            if end is None:  # still streaming: wait
                self._pending = block
                return out
            if end == 0:  # not a call candidate after all (other language)
                out += self._emit(block[:3])
                self._pending = block[3:]
                continue
            piece = block[:end]
            if not self._is_call(piece):
                out += self._emit(piece)
            self._pending = block[end:]
        return out

    def _emit(self, text: str) -> str:
        stripped = text.rstrip(" \t")
        if stripped:  # indentation alone keeps a line's start
            self._line_start = stripped.endswith("\n")
        return text

    def _find_start(self, text: str) -> int | None:
        line_start = self._line_start
        for i, ch in enumerate(text):
            if ch == "`" and text.startswith("```", i):
                return i
            if ch == "{" and line_start:
                return i
            if ch == "\n":
                line_start = True
            elif ch not in " \t":
                line_start = False
        return None

    def _unsafe_tail(self, text: str) -> int:
        """Length of a tail that may grow into a call start."""
        if text.endswith("``"):
            return 2
        if text.endswith("`"):
            return 1
        stripped = text.rstrip(" \t")
        if stripped == "":
            return len(text) if self._line_start else 0
        if stripped.endswith("\n"):
            return len(text) - len(stripped)
        return 0

    def _block_end(self, block: str, final: bool) -> tuple[int | None, bool]:
        """(end, is_fence): end of a candidate block, None if incomplete,
        0 if the fence is another language (stream it as text)."""
        if block.startswith("```"):
            newline = block.find("\n")
            if newline < 0:
                return (len(block) if final else None), True
            lang = block[3:newline].strip().lower()
            if lang not in _CALL_FENCE_LANGS:
                return 0, True
            close = block.find("```", newline + 1)
            if close < 0:
                if final or len(block) > _MAX_HELD:
                    return len(block), True
                return None, True
            return close + 3, True
        end = _object_end(block)
        if end is None:
            if final or len(block) > _MAX_HELD:
                return len(block), False
            return None, False
        return end, False


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
        # Per-model characters/token, learned from Ollama's prompt_eval_count.
        self._chars_per_token: dict[str, float] = {}

    @property
    def chars_per_token(self) -> float:
        """Ratio for budgeting the current model (3 until measured)."""
        measured = self._chars_per_token.get(self.model)
        return measured * _CPT_MARGIN if measured else 3.0

    def _learn_ratio(self, sent_chars: int, prompt_tokens: int) -> None:
        """Fold one request's real token count into the model's ratio."""
        if sent_chars < 200 or prompt_tokens <= 0:
            return  # too small to say anything
        low, high = _CPT_RANGE
        ratio = min(high, max(low, sent_chars / prompt_tokens))
        old = self._chars_per_token.get(self.model)
        self._chars_per_token[self.model] = ratio if old is None else (old + ratio) / 2

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

    async def _tool_mode(self) -> str:
        """How the current model handles tool calls.

        "native" keeps the structured tool_calls path. Anything else falls
        back to text-based tool calling parsed from the model's output.
        """
        method = getattr(self.provider, "tool_mode", None)
        if method is None:
            return "native"
        mode = await method(self.model or "")
        return mode if isinstance(mode, str) else "native"

    def _num_ctx(self) -> int:
        try:
            return int(self.options.get("num_ctx") or DEFAULT_NUM_CTX)
        except (TypeError, ValueError):
            return DEFAULT_NUM_CTX

    def _history_budget(self, overhead_tokens: int) -> int:
        """Tokens available for system prompt + history in one request.

        A quarter of the window (at least 512 tokens) stays free for the
        reply; tool schemas / text-mode instructions are subtracted too.
        """
        num_ctx = self._num_ctx()
        reserve = max(512, num_ctx // 4)
        return num_ctx - reserve - overhead_tokens

    def _max_tool_result_chars(self) -> int:
        """Cap one tool result at about a quarter of the context window."""
        return max(2000, self._num_ctx() // 4 * 3)

    def _outgoing_messages(
        self, text_mode: bool, tools: list[dict] | None = None
    ) -> list[Message]:
        """Return budget-fitted context messages (+ text-mode tool instructions)."""
        instructions = self._tool_instructions() if text_mode else ""
        cpt = self.chars_per_token
        overhead = estimate_tokens(instructions, cpt)
        if tools:
            overhead += estimate_tokens(json.dumps(tools), cpt)
        messages = self.context.to_messages(
            max_tokens=self._history_budget(overhead), chars_per_token=cpt
        )
        if not instructions:
            return messages
        for i, message in enumerate(messages):
            if message.role == Role.SYSTEM:
                messages[i] = message.model_copy(
                    update={"content": message.content + "\n\n" + instructions}
                )
                return messages
        messages.insert(0, Message(role=Role.SYSTEM, content=instructions))
        return messages

    def _tool_instructions(self) -> str:
        """Build a human-readable tool spec for models without native tools."""
        schemas = self._tool_schemas()
        if not schemas:
            return ""
        lines = [
            "You have access to tools. To call one, output a single JSON object "
            "inside a JSON code block. Do not describe the tool call in prose.",
            "",
            "Format:",
            "```json",
            '{"tool": "tool_name", "args": {"key": "value"}}',
            "```",
            "",
            "After a tool runs, its result is provided as a following message. "
            "Use the result to answer, calling more tools if needed.",
            "",
            "Available tools:",
            "",
        ]
        for schema in schemas:
            fn = schema.get("function", {})
            name = fn.get("name", "?")
            description = fn.get("description", "")
            params = fn.get("parameters", {}) or {}
            properties = params.get("properties", {}) or {}
            args = ", ".join(
                f"{pname}: {self._format_param_type(pschema)}"
                for pname, pschema in properties.items()
            ) or "none"
            lines.append(f"- {name}({args}) — {description}")
        return "\n".join(lines)

    @staticmethod
    def _format_param_type(schema: dict[str, Any]) -> str:
        mapping = {
            "string": "str",
            "integer": "int",
            "number": "float",
            "boolean": "bool",
            "array": "list",
            "object": "dict",
            "null": "null",
        }
        if "anyOf" in schema and isinstance(schema["anyOf"], list):
            return " | ".join(
                mapping.get(a.get("type"), "any") for a in schema["anyOf"]
            )
        t = schema.get("type")
        if isinstance(t, list):
            return " | ".join(mapping.get(x, str(x)) for x in t) or "any"
        if isinstance(t, str):
            return mapping.get(t, t)
        return "any"

    @classmethod
    def _parse_tool_calls(cls, text: str) -> list[ToolCall]:
        """Extract JSON tool calls from a model's text output.

        Accepts JSON code blocks, bare JSON objects, and both the
        {"tool": ..., "args": ...} and {"name": ..., "arguments": ...} shapes.
        """
        candidates: list[str] = [
            match.group(1) for match in _JSON_BLOCK_RE.finditer(text)
        ]
        stripped = text.strip()
        if not candidates and stripped.startswith("{") and stripped.endswith("}"):
            candidates.append(stripped)
        if not candidates:
            candidates = cls._scan_bare_objects(text)

        calls: list[ToolCall] = []
        seen: set[str] = set()
        for raw in candidates:
            try:
                obj = json.loads(raw)
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
            if not isinstance(obj, dict):
                continue
            name = obj.get("tool") or obj.get("name")
            if not isinstance(name, str) or not name or name in seen:
                continue
            seen.add(name)
            args = obj.get("args", obj.get("arguments", {}))
            if not isinstance(args, dict):
                args = {}
            calls.append(ToolCall(function=FunctionCall(name=name, arguments=args)))
        return calls

    @staticmethod
    def _scan_bare_objects(text: str) -> list[str]:
        """Find bare JSON objects containing tool/name keys via raw_decode."""
        found: list[str] = []
        decoder = json.JSONDecoder()
        for match in _JSON_KEY_RE.finditer(text):
            start = text.rfind("{", 0, match.start() + 1)
            if start < 0:
                continue
            try:
                obj, _ = decoder.raw_decode(text, start)
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(obj, dict) and (obj.get("tool") or obj.get("name")):
                found.append(json.dumps(obj))
        return found

    async def _generate(self) -> AsyncIterator[StreamChunk]:
        """Generate a response, handling tool calls.

        Context is updated with assistant and tool messages as needed.
        Models verified for native tool calling use structured tool_calls;
        everything else falls back to text-based tool calling.
        """
        mode = await self._tool_mode()
        text_mode = mode != "native"
        send_tools = mode in ("native", "prompt")
        tools = self._tool_schemas() if send_tools else None

        tool_names = {t.name for t in self.registry.list_tools()} if self.registry else set()
        shown = ""  # everything yielded so far: the UI's and the transcript's text

        def visible(text: str) -> str:
            """Separate rounds: the UI and the saved transcript concatenate
            every round's tokens, and a round glued to the previous one could
            leave a code fence open over the rest of the reply."""
            nonlocal shown
            if text and shown and not shown.endswith("\n\n") and new_round[0]:
                text = ("\n" if shown.endswith("\n") else "\n\n") + text
            if text:
                new_round[0] = False
            shown += text
            return text

        new_round = [False]
        for _ in range(self.max_tool_iterations):
            full_response = ""
            tool_calls: list[ToolCall] = []
            hider = _CallHider(tool_names) if text_mode else None

            outgoing = self._outgoing_messages(text_mode, tools)
            sent_chars = sum(len(m.content or "") for m in outgoing)
            if tools:
                sent_chars += len(json.dumps(tools))
            async for chunk in self.provider.chat_stream(
                outgoing,
                model=self.model,
                tools=tools,
                options=self.options or None,
            ):
                if chunk.prompt_eval_count:
                    self._learn_ratio(sent_chars, chunk.prompt_eval_count)
                if chunk.tool_calls:
                    tool_calls.extend(chunk.tool_calls)
                    yield chunk.model_copy(update={"token": ""})
                    continue
                if chunk.token:
                    full_response += chunk.token
                token = hider.feed(chunk.token) if hider and chunk.token else chunk.token
                yield chunk.model_copy(update={"token": visible(token or "")})

            if hider:
                rest = visible(hider.finish())
                if rest:
                    yield StreamChunk(token=rest)

            if not tool_calls and text_mode:
                tool_calls = self._parse_tool_calls(full_response)

            if not tool_calls:
                self.context.add_assistant(full_response)
                return

            self.context.add_assistant_tool_call(
                full_response, [] if text_mode else tool_calls
            )
            new_round[0] = True
            for call in tool_calls:
                name = call.function.name
                args = dict(call.function.arguments)
                await self._notify({"type": "start", "name": name, "args": args})
                started = time.monotonic()
                result = await self.registry.execute(name, args)
                duration_ms = int((time.monotonic() - started) * 1000)
                # A rendered report (AD-039) goes to the reply, not the model.
                artifact = None
                if result.success and isinstance(result.data, dict) and ARTIFACT_KEY in result.data:
                    data = dict(result.data)
                    candidate = data.pop(ARTIFACT_KEY)
                    if isinstance(candidate, dict) and isinstance(candidate.get("html"), str):
                        artifact = candidate
                    result = result.model_copy(update={"data": data})
                await self._notify(
                    {
                        "type": "end",
                        "name": name,
                        "ok": result.success,
                        "duration_ms": duration_ms,
                        "error": "" if result.success else result.error,
                    }
                )
                if artifact is not None:
                    yield StreamChunk(token=visible(artifact_block(
                        str(artifact.get("title", "")), artifact["html"]
                    )))
                content = json.dumps(result.model_dump()) if result.success else result.error
                self.context.add_tool_result(
                    truncate_middle(content or "", self._max_tool_result_chars())
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