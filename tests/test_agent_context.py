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


def test_drop_last_turn():
    ctx = ConversationContext(system_prompt="sys")
    ctx.add_user("u1")
    ctx.add_assistant("a1")
    ctx.add_user("u2")
    ctx.add_assistant_tool_call("", [])
    ctx.add_tool_result("tool output")
    ctx.add_assistant("a2")
    assert ctx.drop_last_turn() == "u2"
    assert [m.content for m in ctx.messages] == ["u1", "a1"]
    assert ctx.drop_last_turn() == "u1"
    assert ctx.drop_last_turn() is None
    assert ctx.messages == []

def test_system_prompt_settable():
    ctx = ConversationContext()
    ctx.system_prompt = "Custom prompt"
    assert ctx.to_messages()[0].content == "Custom prompt"


# ---- Token budgeting ------------------------------------------------------

from nova.agent.context import estimate_tokens, truncate_middle  # noqa: E402
from nova.llm.models import FunctionCall, ToolCall  # noqa: E402


def test_estimate_tokens_is_conservative():
    assert estimate_tokens("") == 0
    assert estimate_tokens("abc") == 1
    # ~4 chars/token is typical; the estimate must not undercount that.
    assert estimate_tokens("x" * 400) >= 100


def test_truncate_middle_keeps_head_and_tail():
    text = "HEAD" + "m" * 10_000 + "TAIL"
    out = truncate_middle(text, 500)
    assert len(out) <= 500
    assert out.startswith("HEAD") and out.endswith("TAIL")
    assert "characters truncated" in out
    assert truncate_middle("short", 500) == "short"


def test_to_messages_without_budget_returns_everything():
    ctx = ConversationContext()
    for i in range(20):
        ctx.add_user(f"u{i}")
        ctx.add_assistant(f"a{i}")
    assert len(ctx.to_messages()) == 41


def test_budget_drops_oldest_and_keeps_system_prompt():
    ctx = ConversationContext(system_prompt="SYS")
    for i in range(50):
        ctx.add_user(f"question {i} " + "x" * 300)
        ctx.add_assistant(f"answer {i} " + "y" * 300)
    msgs = ctx.to_messages(max_tokens=1000)
    assert msgs[0].role == Role.SYSTEM and msgs[0].content == "SYS"
    assert 1 < len(msgs) < 101
    assert msgs[1].role == Role.USER  # window starts at a user turn
    assert msgs[-1].content.startswith("answer 49")
    # stored history is untouched
    assert len(ctx.messages) == 100


def test_budget_never_splits_tool_sequences():
    ctx = ConversationContext()
    for i in range(10):
        ctx.add_user(f"u{i} " + "x" * 200)
        ctx.add_assistant_tool_call(
            "", [ToolCall(function=FunctionCall(name="t", arguments={}))]
        )
        ctx.add_tool_result("r" * 600)
        ctx.add_assistant(f"a{i}")
    for budget in range(100, 2000, 37):
        msgs = ctx.to_messages(max_tokens=budget)
        assert msgs[1].role == Role.USER


def test_budget_always_keeps_current_turn():
    ctx = ConversationContext()
    ctx.add_user("old")
    ctx.add_assistant("old answer")
    ctx.add_user("now " + "z" * 5000)
    ctx.add_tool_result("big " + "r" * 5000)
    msgs = ctx.to_messages(max_tokens=10)  # far too small
    assert [m.role for m in msgs] == [Role.SYSTEM, Role.USER, Role.TOOL]
    assert msgs[1].content.startswith("now")
