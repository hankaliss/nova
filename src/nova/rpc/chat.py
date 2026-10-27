"""Chat turns: sending, streaming, stopping, regenerating, permission and choice prompts."""

import asyncio
import json
import time
from typing import Any

from nova.tools.tool import RiskLevel
from nova.tools.report import strip_artifacts


class ChatMixin:
    """Part of :class:`nova.rpc.core.NovaRpc`; state lives on the instance."""

    def start_conversation(self) -> None:
        if self._store is None:
            self._conversation_id = None
            return
        self._conversation_id = self._store.create_conversation()

    def load_history(self) -> None:
        """Seed context from the last stored conversation."""
        if self._store is None or self._agent is None:
            return
        conversations = self._store.list_conversations()
        if not conversations:
            self.start_conversation()
            return
        from nova.llm.models import Role

        self._conversation_id = conversations[0]["id"]
        for message in self._store.get_messages(self._conversation_id):
            if message.role == Role.USER:
                self._last_user_prompt = message.content
                self._agent.context.add_user(message.content)
                self._emit_message("user", message.content, False, message.created_at)
            elif message.role == Role.ASSISTANT:
                # Saved tool artifacts are for the UI, not the model (AD-039).
                self._agent.context.add_assistant(strip_artifacts(message.content))
                self._emit_message("assistant", message.content, False, message.created_at)

    def _emit_message(
        self, role: str, content: str, streaming: bool, created_at: int | None = None
    ) -> None:
        """Emit a messageAdded event, tagging it with the stored timestamp."""
        if created_at is None:
            created_at = int(time.time())
        self.emit("messageAdded", role, content, streaming, created_at)

    async def _on_tool_event(self, event: dict[str, Any]) -> None:
        kind = event.get("type")
        if kind == "start":
            self.emit("toolStarted", event["name"], json.dumps(event.get("args", {}), default=str))
        elif kind == "end":
            self.emit(
                "toolFinished",
                event["name"],
                bool(event.get("ok")),
                int(event.get("duration_ms", 0)),
                str(event.get("error", "")),
            )

    async def _request_permission(self, tool, args) -> bool:
        if self._pending_permission is not None:
            return False
        self._pending_permission = asyncio.get_running_loop().create_future()
        self._grant_tool = tool
        self.emit(
            "permissionRequested",
            tool.name,
            tool.risk.value,
            tool.permission,
            json.dumps(args, default=str),
        )
        return await self._pending_permission

    async def sendMessage(self, text: str) -> None:
        if self._agent is None or not text.strip():
            return
        self._send_task = asyncio.current_task()
        self._last_user_prompt = text
        self._save_message("user", text)
        self._emit_message("user", text, False)
        self._emit_message("assistant", "", True)


        full_response = ""
        stats: dict[str, Any] = {
            "prompt_tokens": 0,
            "tokens": 0,
            "tools": 0,
            "model": self._agent.model or "",
        }
        self.emit("streamingChanged", True)
        try:
            # A context fit loads the model at trial sizes; let it finish
            # first instead of competing for the GPU. The message is already
            # on screen and Stop works meanwhile.
            if self._context_fitting():
                self.emit("activity_note", "Measuring how much context fits on your GPU…")
                await self._await_context_fit()
                self.emit("activity_note", "")  # the model may go straight to tools
            self._agent.context.memory_note = self._memory_note(text)
            async for chunk in self._agent.send(text):
                if chunk.token:
                    full_response += chunk.token
                    self.emit("streamUpdated", chunk.token)
                if chunk.prompt_eval_count:
                    stats["prompt_tokens"] = max(stats["prompt_tokens"], chunk.prompt_eval_count)
                if chunk.eval_count:
                    stats["tokens"] = max(stats["tokens"], chunk.eval_count)
                if chunk.tool_calls:
                    stats["tools"] += len(chunk.tool_calls)
                if chunk.prompt_eval_duration_ns:
                    stats["prompt_eval_ns"] = chunk.prompt_eval_duration_ns
                if chunk.eval_duration_ns:
                    stats["eval_ns"] = chunk.eval_duration_ns
                if chunk.total_duration_ns:
                    stats["total_ns"] = chunk.total_duration_ns
            self.emit("streamCompleted")
        except asyncio.CancelledError:
            self.emit("statusChanged", "Generation stopped")
            self.emit("streamCompleted")
        except Exception as e:
            self.emit("statusChanged", f"Error: {e}")
            self.emit("streamCompleted")
        finally:
            if self._send_task is asyncio.current_task():
                self._send_task = None
            self.emit("streamingChanged", False)
            if full_response:
                self._save_message("assistant", full_response)
            if stats["tokens"] and full_response:
                self.emit("responseMetrics", json.dumps(stats))
            if self._skill_loader is not None:
                self.emit("skill_list", json.loads(self._skill_loader.to_json()))
            if full_response:
                asyncio.ensure_future(self._check_context_spill())
            self._emit_memories()

    def _save_message(self, role: str, content: str) -> None:
        if self._store is None or self._conversation_id is None or not content:
            return
        from nova.llm.models import Role

        self._store.add_message(self._conversation_id, Role(role), content)
        if role == "user" and self._store.auto_title(self._conversation_id, content):
            self.emit("conversation_list", self._store.list_conversations())

    def stopGeneration(self) -> None:
        if self._send_task is not None and not self._send_task.done():
            self._send_task.cancel()

    def regenerateLast(self) -> None:
        """Answer the last question again: drop the last turn from the
        context and the store (sendMessage saves the question anew)."""
        if self._agent is None:
            return
        if self._send_task is not None and not self._send_task.done():
            return
        # From this conversation's context: _last_user_prompt can belong to
        # another conversation after a switch.
        text = self._agent.context.drop_last_turn()
        if not text:
            return
        if self._store is not None and self._conversation_id is not None:
            self._store.delete_last_turn(self._conversation_id)
        self._send_task = asyncio.ensure_future(self.sendMessage(text))

    def clearHistory(self) -> None:
        self.stopGeneration()
        # If the active conversation is already empty, just reset the agent
        # instead of creating another blank one.
        if (
            self._store is not None
            and self._conversation_id is not None
            and not self._store.get_messages(self._conversation_id)
        ):
            if self._agent is not None:
                self._agent.reset()
            self._last_user_prompt = ""
            # Still emit events so the frontend stays in sync.
            self.emit("conversationSwitched", self._conversation_id)
            if self._store is not None:
                self.emit("conversation_list", self._store.list_conversations())
            return
        self.start_conversation()
        if self._agent is not None:
            self._agent.reset()
        self._last_user_prompt = ""
        if self._store is not None:
            self.emit("conversation_list", self._store.list_conversations())

    def respondPermission(self, approved: bool, remember: bool = False) -> None:
        if self._pending_permission is None:
            return
        future, self._pending_permission = self._pending_permission, None
        tool = self._grant_tool
        self._grant_tool = None
        if (
            approved
            and remember
            and tool is not None
            and tool.risk != RiskLevel.DESTRUCTIVE
        ):
            if self._store is not None:
                self._store.set_setting(f"perm_grant_{tool.permission}", "true")
            if self._registry.permissions is not None:
                self._registry.permissions.grant(tool.permission)
        future.set_result(approved)

    async def request_choices(self, question: str, options: list[str]) -> str:
        """Present a multiple-choice question and wait for the selection.

        Emits a `choicesRequested` event to the frontend and blocks until the
        user picks an option (via selectChoice). Mirrors the permission flow.
        """
        if self._pending_choice is not None:
            raise RuntimeError("A multiple-choice question is already pending")
        if not isinstance(question, str) or not question.strip():
            raise ValueError("nova.presentChoices: question must be non-empty")
        opts = [str(o).strip() for o in (options or []) if str(o).strip()]
        if len(opts) < 2:
            raise ValueError("nova.presentChoices: at least 2 options required")
        opts = opts[:9]
        self._pending_choice = asyncio.get_running_loop().create_future()
        self._choice_options = opts
        self.emit("choicesRequested", question, json.dumps(opts, default=str))
        try:
            return await self._pending_choice
        finally:
            self._pending_choice = None
            self._choice_options = []

    def selectChoice(self, index: int) -> str:
        """Resolve the pending choice with the option at the given index."""
        if self._pending_choice is None:
            return ""
        future, self._pending_choice = self._pending_choice, None
        options, self._choice_options = self._choice_options, []
        if 0 <= index < len(options):
            future.set_result(options[index])
            return options[index]
        future.set_result("")
        return ""
