"""Tests for ConversationContext."""

from nova.agent.context import ConversationContext, DEFAULT_SYSTEM_PROMPT
from nova.llm.models import Role


def test_default_system_prompt():
    assert "Nova" in DEFAULT_SYSTEM_PROMPT
    assert len(DEFAULT_SYSTEM_PROMPT) > 0


def test_context_initial_state():
    ctx = ConversationContext()
    msgs = ctx.to_messages()
    assert len(msgs) == 1
    assert msgs[0].role == Role.SYSTEM
    assert msgs[0].content == DEFAULT_SYSTEM_PROMPT


def test_add_user_message():
    ctx = ConversationContext()
    ctx.add_user("Hello")
    msgs = ctx.to_messages()
    assert len(msgs) == 2
    assert msgs[1].role == Role.USER
    assert msgs[1].content == "Hello"


def test_add_assistant_message():
    ctx = ConversationContext()
    ctx.add_user("Hi")
    ctx.add_assistant("Hello!")
    msgs = ctx.to_messages()
    assert len(msgs) == 3
    assert msgs[2].role == Role.ASSISTANT
    assert msgs[2].content == "Hello!"


def test_conversation_flow():
    ctx = ConversationContext()
    ctx.add_user("What is 2+2?")
    ctx.add_assistant("4")
    ctx.add_user("And 3+3?")
    ctx.add_assistant("6")
    msgs = ctx.to_messages()
    assert len(msgs) == 5  # system + 4 messages


def test_clear():
    ctx = ConversationContext()
    ctx.add_user("Hello")
    ctx.add_assistant("Hi")
    ctx.clear()
    msgs = ctx.to_messages()
    assert len(msgs) == 1  # only system prompt


def test_max_messages_trims():
    ctx = ConversationContext(max_messages=4)
    for i in range(10):
        ctx.add_user(f"msg {i}")
        ctx.add_assistant(f"reply {i}")
    # Should keep only last 4 messages
    assert len(ctx._messages) == 4


def test_messages_returns_copy():
    ctx = ConversationContext()
    ctx.add_user("Hello")
    msgs = ctx.messages
    msgs.clear()
    assert len(ctx.messages) == 1


def test_custom_system_prompt():
    ctx = ConversationContext(system_prompt="Custom prompt")
    msgs = ctx.to_messages()
    assert msgs[0].content == "Custom prompt"


def test_to_messages_returns_full_list():
    ctx = ConversationContext()
    ctx.add_user("test")
    ctx.add_assistant("response")
    msgs = ctx.to_messages()
    assert msgs[0].role == Role.SYSTEM
    assert msgs[1].role == Role.USER
    assert msgs[2].role == Role.ASSISTANT


def test_pop_last_assistant_and_user():
    ctx = ConversationContext()
    ctx.add_user("u1")
    ctx.add_assistant("a1")
    ctx.add_user("u2")
    ctx.add_assistant("a2")

    assert ctx.pop_last_assistant().content == "a2"
    assert ctx.pop_last_user().content == "u2"
    assert ctx.pop_last_assistant().content == "a1"
    assert ctx.pop_last_user().content == "u1"
    assert ctx.pop_last_assistant() is None
    assert ctx.pop_last_user() is None


def test_system_prompt_settable():
    ctx = ConversationContext()
    ctx.system_prompt = "Custom prompt"
    assert ctx.to_messages()[0].content == "Custom prompt"
