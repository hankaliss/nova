"""Conversation list: switch, delete, rename."""

import json
from nova.tools.report import strip_artifacts


class ConversationsMixin:
    """Part of :class:`nova.rpc.core.NovaRpc`; state lives on the instance."""

    def listConversations(self) -> str:
        if self._store is None:
            return "[]"
        conversations = self._store.list_conversations()
        self.emit("conversation_list", conversations)
        return json.dumps(conversations)

    def switchConversation(self, conversation_id: int) -> None:
        if self._store is None:
            return
        if self._agent is None:
            return
        self.stopGeneration()
        # Emit the switch BEFORE replaying messages: the UI clears its view
        # on conversationSwitched, so replayed messageAdded events must
        # arrive after the clear or the chat is wiped.
        self._conversation_id = conversation_id
        self._agent.reset()
        self.emit("conversationSwitched", conversation_id)
        from nova.llm.models import Role

        for message in self._store.get_messages(conversation_id):
            if message.role == Role.USER:
                self._agent.context.add_user(message.content)
                self._emit_message("user", message.content, False, message.created_at)
            elif message.role == Role.ASSISTANT:
                # Saved tool artifacts are for the UI, not the model (AD-039).
                self._agent.context.add_assistant(strip_artifacts(message.content))
                self._emit_message("assistant", message.content, False, message.created_at)
        conversations = self._store.list_conversations()
        self.emit("conversation_list", conversations)

    def deleteConversation(self, conversation_id: int) -> None:
        if self._store is None:
            return
        self.stopGeneration()
        self._store.delete_conversation(conversation_id)
        if self._conversation_id == conversation_id:
            self._conversation_id = None
            if self._agent is not None:
                self._agent.reset()
        self.emit("conversationDeleted", conversation_id)
        conversations = self._store.list_conversations()
        self.emit("conversation_list", conversations)

    def renameConversation(self, conversation_id: int, title: str) -> None:
        if self._store is None:
            return
        self._store.rename_conversation(conversation_id, title)
