"""Rendered tool artifacts (AD-039): report kit and the Agent's handling."""

import json

import pytest

from nova.agent.agent import Agent
from nova.llm.models import Role, StreamChunk
from nova.tools.registry import ToolRegistry
from nova.tools.report import (
    ARTIFACT_KEY,
    ARTIFACT_MARKER,
    Bar,
    Finding,
    Report,
    Stat,
    artifact_block,
    esc,
    strip_artifacts,
    with_artifact,
)
from nova.tools.tool import RiskLevel, Tool


def sample_report():
    return (
        Report("System doctor", subtitle="Since boot", kicker="Nova skill")
        .stats([Stat("Failed units", 2, tone="danger"), Stat("Errors", 14, tone="warn", hint="last boot")])
        .section("Errors by unit", note="top 5")
        .bars([Bar("kwin_wayland", 9, 9, "9 errors", "warn")])
        .table(["Unit", "Result"], [["bluetooth.service", "failed"]], tones=["danger"], mono={0})
        .findings([Finding("Segfault in `foo`", detail="<script>alert(1)</script>", tone="danger", tag="crash")])
        .callout("No OOM kills", tone="ok")
        .chips([("kernel 7.2", "info")])
    )


def test_report_is_a_complete_static_page():
    html = sample_report().html()
    assert html.startswith("<!DOCTYPE html>") and "<title>System doctor</title>" in html
    for text in ("Failed units", "Errors by unit", "bluetooth.service", "No OOM kills", "kernel 7.2"):
        assert text in html
    assert "<script>" not in html  # escaped, and the kit itself has no scripts
    assert "&lt;script&gt;" in html
    assert "`" not in html  # backticks would end the ``` fence in the reply


def test_esc():
    assert esc('<a href="x">`') == "&lt;a href=&quot;x&quot;&gt;&#96;"


def test_with_artifact_and_block_round_trip():
    result = with_artifact({"failed": 2}, sample_report())
    assert result["failed"] == 2 and result[ARTIFACT_KEY]["title"] == "System doctor"
    block = artifact_block("System doctor", result[ARTIFACT_KEY]["html"])
    assert block.startswith("\n\n```html\n" + ARTIFACT_MARKER)
    reply = "Here is what I found." + block + "Bluetooth keeps failing."
    assert strip_artifacts(reply) == "Here is what I found.\n\nBluetooth keeps failing."


def test_block_neutralizes_fences_in_html():
    block = artifact_block("t", "<p>```</p>")
    assert block.count("```") == 2  # only the fence itself


class Provider:
    def __init__(self, replies):
        self.replies = list(replies)

    async def tool_mode(self, model):
        return "native"

    async def chat_stream(self, messages, model="", tools=None, options=None):
        reply = self.replies.pop(0)
        if isinstance(reply, str):
            yield StreamChunk(token=reply, done=True)
        else:
            yield StreamChunk(token="", tool_calls=reply, done=True)


@pytest.mark.asyncio
async def test_agent_streams_artifact_and_keeps_it_from_the_model():
    from nova.llm.models import FunctionCall, ToolCall

    registry = ToolRegistry()
    registry.register(Tool(
        name="doctor.check", description="d", risk=RiskLevel.READ,
        implementation=lambda: with_artifact({"failed": 2}, sample_report()),
    ))
    call = [ToolCall(function=FunctionCall(name="doctor.check", arguments={}))]
    agent = Agent(provider=Provider([call, "Two units failed."]), registry=registry, model="m")

    reply = await agent.send_sync("what's wrong?")

    assert ARTIFACT_MARKER in reply and reply.rstrip().endswith("Two units failed.")
    tool_msg = next(m for m in agent.context.messages if m.role == Role.TOOL)
    assert json.loads(tool_msg.content)["data"] == {"failed": 2}
    assert "DOCTYPE" not in "".join(m.content or "" for m in agent.context.messages)
