"""Integration tests for the MCP client/manager against a real fake server.

The fake server (tests/fake_mcp_server.py) is hand-rolled JSON-RPC over
stdio, so these tests also verify our client speaks plain conformant
JSON-RPC. Skipped entirely when the mcp SDK is not installed.
"""

import asyncio
import json
import sys
import time
from pathlib import Path

import pytest

pytest.importorskip("mcp")

from nova.mcp.client import McpClient  # noqa: E402
from nova.mcp.config import McpServerConfig  # noqa: E402
from nova.mcp.manager import McpManager  # noqa: E402
from nova.security.permissions import PermissionSystem  # noqa: E402
from nova.tools.registry import ToolRegistry  # noqa: E402
from nova.tools.tool import RiskLevel  # noqa: E402

FAKE_SERVER = Path(__file__).parent / "fake_mcp_server.py"


def make_config(**overrides) -> McpServerConfig:
    defaults = {
        "command": sys.executable,
        "args": [str(FAKE_SERVER)],
    }
    defaults.update(overrides)
    return McpServerConfig(**defaults)


async def wait_for(predicate, timeout: float = 10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.05)
    return False


@pytest.mark.asyncio
async def test_client_connects_and_lists_tools():
    client = McpClient("fake", make_config())
    try:
        await client.start()
        assert await wait_for(lambda: client.state == "connected"), client.status()
        names = {t.name for t in client.tools}
        assert names == {"echo", "boom", "needs_login", "needs_login_ok"}
    finally:
        await client.shutdown()


@pytest.mark.asyncio
async def test_client_call_tool_round_trip():
    client = McpClient("fake", make_config())
    try:
        await client.start()
        assert await wait_for(lambda: client.connected)
        result = await client.call_tool("echo", {"text": "hello nova"})
        assert result.content[0].text == "echo: hello nova"
        assert not (getattr(result, "is_error", False) or getattr(result, "isError", False))
    finally:
        await client.shutdown()


@pytest.mark.asyncio
async def test_client_restarts_after_process_death():
    client = McpClient("fake", make_config(env={"FAKE_EXIT_AFTER": "tools_list"}))
    try:
        await client.start()
        # First connection dies right after tools/list; the health ping
        # needs up to HEALTH_INTERVAL_S to notice → failed…
        assert await wait_for(lambda: client.state == "failed", timeout=15), client.status()
        # …then the backoff loop starts a new process (state returns to
        # starting, which fails again — the cycle proves auto-restart).
        deadline = time.monotonic() + 15
        saw_new_attempt = False
        while time.monotonic() < deadline:
            if client.state in ("starting", "connected"):
                saw_new_attempt = True
                break
            await asyncio.sleep(0.05)
        assert saw_new_attempt, f"no restart attempt observed: {client.status()}"
    finally:
        await client.stop()  # break the restart cycle
        await client.shutdown()


@pytest.mark.asyncio
async def test_manager_registers_tools_into_registry():
    registry = ToolRegistry()
    manager = McpManager(registry)
    client = McpClient("fake", make_config(), on_state_change=manager._on_state)
    manager.clients["fake"] = client
    try:
        await client.start()
        assert await wait_for(lambda: client.state == "connected")
        assert await wait_for(
            lambda: any(t.name == "mcp.fake.echo" for t in registry.list_tools())
        )
        schema_names = [
            s["function"]["name"] for s in registry.to_schemas()
        ]
        assert "mcp.fake.echo" in schema_names
        tool = registry.get("mcp.fake.echo")
        assert tool.risk is RiskLevel.WRITE
        assert tool.parameters["type"] == "object"

        registry.permissions.grant("mcp.fake.echo")  # simulate remembered approval
        result = await registry.execute("mcp.fake.echo", {"text": "hi"})
        assert result.success and result.data == "echo: hi"
    finally:
        await client.shutdown()


@pytest.mark.asyncio
async def test_manager_tool_error_becomes_failed_result():
    registry = ToolRegistry()
    manager = McpManager(registry)
    client = McpClient("fake", make_config(), on_state_change=manager._on_state)
    manager.clients["fake"] = client
    try:
        await client.start()
        assert await wait_for(lambda: any(t.name == "mcp.fake.boom" for t in registry.list_tools()))
        registry.permissions.grant("mcp.fake.boom")
        result = await registry.execute("mcp.fake.boom", {})
        assert not result.success
        assert "boom failed" in result.error
    finally:
        await client.shutdown()


@pytest.mark.asyncio
async def test_auth_challenge_surfaces_and_clears():
    registry = ToolRegistry()
    auth_events: list[tuple[str, dict]] = []
    manager = McpManager(
        registry,
        notify=lambda _st: None,
        on_auth=lambda name, info: auth_events.append((name, info)),
    )
    client = McpClient("fake", make_config(), on_state_change=manager._on_state)
    manager.clients["fake"] = client
    challenge = {
        "url": "https://github.com/login/device?user_code=WDJB-MJHT",
        "code": "WDJB-MJHT",
    }

    async def _signal_auth(delay: float = 0.05):
        """Simulate auth completion by signaling auth_event after a short delay."""
        await asyncio.sleep(delay)
        client.auth_event.set()

    try:
        await client.start()
        assert await wait_for(
            lambda: any(t.name == "mcp.fake.needs_login" for t in registry.list_tools())
        )
        registry.permissions.grant("mcp.fake.needs_login")

        # Failing call: now waits for auth. Signal from background.
        task = asyncio.create_task(_signal_auth())
        result = await registry.execute("mcp.fake.needs_login", {})
        await task
        assert not result.success
        assert "requires browser authorization" in result.error
        # Raw server instructions never reach the chat.
        assert "login/device" not in result.error
        assert client.auth == challenge
        assert auth_events == [("fake", challenge)]
        assert manager.status()[0]["auth"] == challenge

        # Repeated failures don't re-fire the UI event.
        await registry.execute("mcp.fake.needs_login", {})
        assert len(auth_events) == 1

        # While a login is pending, retries must NOT reach the server:
        # every real call restarts its OAuth flow and invalidates the
        # authorization URL already opened in the browser.
        calls = {"n": 0}
        original_call = client.call_tool

        async def counting(tool, args):
            calls["n"] += 1
            return await original_call(tool, args)

        registry.permissions.grant("mcp.fake.echo")
        client.call_tool = counting
        try:
            blocked = await registry.execute("mcp.fake.echo", {"text": "hi"})
            assert not blocked.success
            assert "requires browser authorization" in blocked.error

            # After the probe interval elapses, one real call is let
            # through: if the user completed the login meanwhile it simply
            # works instead of demanding a button press.
            client.auth_probed_at -= 60.0
            task = asyncio.create_task(_signal_auth())
            probed = await registry.execute("mcp.fake.needs_login", {})
            await task
            assert not probed.success  # fake server challenges again
            assert calls["n"] == 1
            assert client.auth == challenge
        finally:
            del client.call_tool

        # A changed challenge URL re-notifies (the old link is dead).
        challenge2 = {
            **challenge,
            "url": "https://github.com/login/device?user_code=ABCD-EFGH",
        }
        manager._set_auth(client, challenge2)
        assert auth_events[-1] == ("fake", challenge2)
        manager._set_auth(client, challenge2)
        assert len(auth_events) == 2

        # User presses "I've signed in" → gate released, calls work again.
        assert manager.request_retry("fake") is True
        assert client.auth is None
        assert manager.status()[0]["auth"] is None
        ok = await registry.execute("mcp.fake.echo", {"text": "hi"})
        assert ok.success
        # Already released: retrying is a no-op.
        assert manager.request_retry("fake") is False
    finally:
        await client.shutdown()


@pytest.mark.asyncio
async def test_auth_challenge_in_success_result_is_caught():
    """github-mcp-server delivers its OAuth login request as a *successful*
    result (no isError). A verification-style URL must still trigger the
    login flow; ordinary link-bearing output must not."""
    registry = ToolRegistry()
    auth_events: list[tuple[str, dict]] = []
    manager = McpManager(
        registry,
        notify=lambda _st: None,
        on_auth=lambda name, info: auth_events.append((name, info)),
    )
    client = McpClient("fake", make_config(), on_state_change=manager._on_state)
    manager.clients["fake"] = client

    async def _signal_auth(delay: float = 0.05):
        await asyncio.sleep(delay)
        client.auth_event.set()

    try:
        await client.start()
        assert await wait_for(
            lambda: any(t.name == "mcp.fake.needs_login_ok" for t in registry.list_tools())
        )
        registry.permissions.grant("mcp.fake.needs_login_ok")

        task = asyncio.create_task(_signal_auth())
        result = await registry.execute("mcp.fake.needs_login_ok", {})
        await task
        assert not result.success
        assert "requires browser authorization" in result.error
        assert "login/oauth/authorize" not in result.error  # raw URL stays away
        assert client.auth is not None
        assert client.auth["url"].startswith("https://github.com/login/oauth/authorize")
        assert client.auth["code"] == ""
        assert len(auth_events) == 1

        # Ordinary successful output containing links must NOT trigger auth;
        # after the gate is released it just runs like any normal success.
        assert manager.request_retry("fake") is True
        registry.permissions.grant("mcp.fake.echo")
        await registry.execute("mcp.fake.echo", {"text": "see https://example.com"})
        assert client.auth is None
        assert len(auth_events) == 1
    finally:
        await client.shutdown()


@pytest.mark.asyncio
async def test_write_risk_goes_through_permission_gate():
    registry = ToolRegistry(permissions=PermissionSystem())  # no approver → deny
    manager = McpManager(registry)
    client = McpClient("fake", make_config(), on_state_change=manager._on_state)
    manager.clients["fake"] = client
    try:
        await client.start()
        assert await wait_for(lambda: any(t.name == "mcp.fake.echo" for t in registry.list_tools()))
        result = await registry.execute("mcp.fake.echo", {"text": "x"})
        assert not result.success
        assert "denied" in result.error.lower()

        # Granting the exact permission key lets it through.
        registry.permissions.grant("mcp.fake.echo")
        result = await registry.execute("mcp.fake.echo", {"text": "y"})
        assert result.success and result.data == "echo: y"
    finally:
        await client.shutdown()


@pytest.mark.asyncio
async def test_stop_unregisters_tools():
    registry = ToolRegistry()
    manager = McpManager(registry)
    client = McpClient("fake", make_config(), on_state_change=manager._on_state)
    manager.clients["fake"] = client
    await client.start()
    assert await wait_for(lambda: any(t.name.startswith("mcp.fake.") for t in registry.list_tools()))

    await client.stop()
    assert all(not t.name.startswith("mcp.fake.") for t in registry.list_tools())
    assert client.state == "disabled"


@pytest.mark.asyncio
async def test_save_server_live_applies(tmp_path):
    registry = ToolRegistry()
    manager = McpManager(registry)
    manager.path = tmp_path / "mcp.json"
    await manager.save_server(
        "lazy",
        {"command": sys.executable, "args": [str(FAKE_SERVER)], "enabled": False},
    )
    # Disabled entry: present in status but no process, no tools.
    statuses = {s["name"]: s for s in manager.status()}
    assert statuses["lazy"]["state"] == "disabled"
    assert not any(t.name.startswith("mcp.lazy.") for t in registry.list_tools())

    assert statuses["lazy"]["tools"] == []

    ok = await manager.set_enabled("lazy", True)
    assert ok
    assert await wait_for(lambda: any(t.name == "mcp.lazy.echo" for t in registry.list_tools()))
    # The status lists each tool for the UI ("Show tools"), with Nova's risk.
    tools = {t["name"]: t for t in manager.status()[0]["tools"]}
    assert set(tools) == {"echo", "boom", "needs_login", "needs_login_ok"}
    assert tools["echo"]["risk"] in {"read", "write", "destructive"}
    assert isinstance(tools["echo"]["description"], str)
    assert "\n" not in tools["echo"]["description"]
    saved = json.loads(manager.path.read_text())
    assert saved["mcpServers"]["lazy"]["enabled"] is True

    ok = await manager.set_enabled("lazy", False)
    assert ok
    assert all(not t.name.startswith("mcp.lazy.") for t in registry.list_tools())
    await manager.shutdown()


@pytest.mark.asyncio
async def test_stderr_auth_hint_releases_gate():
    """The server reports 'authorization complete' on stderr → the pending
    challenge is dropped immediately, without probe interval or button.
    With auto-retry, the tap unblocks the waiting call which retries."""
    registry = ToolRegistry()
    manager = McpManager(registry, notify=lambda _st: None)
    client = McpClient(
        "fake", make_config(env={"FAKE_AUTH_REPORT": "1"}), on_state_change=manager._on_state
    )
    manager.clients["fake"] = client
    try:
        await client.start()
        assert await wait_for(
            lambda: any(t.name == "mcp.fake.needs_login" for t in registry.list_tools())
        )
        registry.permissions.grant("mcp.fake.needs_login")

        # The call detects an auth challenge and waits for auth_event.
        # The fake server prints the completion line ~0.4 s later on stderr;
        # the tap sets auth_event, unblocking the call which retries.
        start = time.monotonic()
        result = await registry.execute("mcp.fake.needs_login", {})
        elapsed = time.monotonic() - start
        assert not result.success
        assert "requires browser authorization" in result.error
        # The tap released the gate quickly (< 5s); without it the call
        # would wait up to 300s.
        assert elapsed < 5.0, f"auth gate was not released quickly ({elapsed:.1f}s)"
        # Auth is set again by the retry (fake server always challenges).
        assert client.auth is not None
        assert manager.status()[0]["auth"] is not None
    finally:
        await client.shutdown()
