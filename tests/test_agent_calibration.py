"""Token estimates calibrated with Ollama's prompt_eval_count (AD-038)."""

import pytest

from nova.agent.agent import Agent
from nova.agent.context import estimate_tokens
from nova.llm.models import StreamChunk


class Provider:
    def __init__(self, prompt_tokens):
        self.prompt_tokens = prompt_tokens
        self.sent = []

    async def tool_mode(self, model):
        return "native"

    async def chat_stream(self, messages, model="", tools=None, options=None):
        self.sent.append(messages)
        yield StreamChunk(token="ok", done=True, prompt_eval_count=self.prompt_tokens)


def test_estimate_with_ratio():
    assert estimate_tokens("x" * 30) == 10
    assert estimate_tokens("x" * 30, 5.0) == 6


@pytest.mark.asyncio
async def test_ratio_learned_from_a_reply():
    agent = Agent(provider=Provider(prompt_tokens=100), model="m", system_prompt="s" * 480)
    assert agent.chars_per_token == 3.0
    await agent.send_sync("hi" * 10)  # 480 + 20 = 500 chars sent
    assert agent._chars_per_token["m"] == pytest.approx(5.0)
    assert agent.chars_per_token == pytest.approx(4.5)  # with the safety margin


@pytest.mark.asyncio
async def test_ratio_is_clamped_and_smoothed():
    agent = Agent(provider=Provider(prompt_tokens=10), model="m", system_prompt="s" * 1000)
    await agent.send_sync("x")
    assert agent._chars_per_token["m"] == 6.0  # 100 chars/token is clamped
    agent.provider.prompt_tokens = 1000
    await agent.send_sync("x")
    assert agent._chars_per_token["m"] == pytest.approx((6.0 + 2.0) / 2)


@pytest.mark.asyncio
async def test_tiny_prompts_teach_nothing_and_models_are_separate():
    agent = Agent(provider=Provider(prompt_tokens=10), model="a", system_prompt="s")
    await agent.send_sync("hi")
    assert agent._chars_per_token == {}
    agent.system_prompt = None
    agent.context.system_prompt = "s" * 500
    await agent.send_sync("hi")
    agent.model = "b"
    assert agent.chars_per_token == 3.0


@pytest.mark.asyncio
async def test_calibrated_budget_keeps_more_history():
    provider = Provider(prompt_tokens=0)
    agent = Agent(provider=provider, model="m", options={"num_ctx": 2048})
    for i in range(30):
        agent.context.add_user(f"question {i} " + "w" * 150)
        agent.context.add_assistant(f"answer {i} " + "w" * 150)
    before = len(agent._outgoing_messages(text_mode=False))
    agent._chars_per_token["m"] = 5.0
    after = len(agent._outgoing_messages(text_mode=False))
    assert after > before
