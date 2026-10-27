"""Tests for the persistent shell session (AD-022).

These spawn a real ``bash`` on a PTY (no GPU, no Ollama) — hermetic enough
for unit tests and the closest thing to the real behaviour.
"""

import asyncio
import shutil

import pytest

from nova.shell.session import ShellSession, TerminalView
from nova.tools.registry import ToolRegistry
from nova.tools.tool import RiskLevel


@pytest.fixture
def session():
    s = ShellSession(shell="/bin/bash", spawn_timeout=5.0)
    yield s
    s.close()


@pytest.mark.asyncio
async def test_run_basic(session):
    result = await session.run("echo hello; pwd")
    assert result["returncode"] == 0
    assert result["output"] == "hello\n" + result["cwd"]
    assert result["cwd"] and "/" in result["cwd"]


@pytest.mark.asyncio
async def test_cwd_and_env_persist(session):
    await session.run("cd /tmp; export PERSIST_VAR=42")
    result = await session.run("pwd; printf '%s' \"$PERSIST_VAR\"")
    assert result["cwd"] == "/tmp"
    assert result["output"] == "/tmp\n42"


@pytest.mark.asyncio
async def test_nonzero_returncode(session):
    result = await session.run("false")
    assert result["returncode"] == 1
    assert result["output"] == ""


@pytest.mark.asyncio
async def test_command_without_trailing_newline(session):
    result = await session.run('printf "no-newline"', timeout=5)
    assert result["output"] == "no-newline"


@pytest.mark.asyncio
async def test_marker_like_output_is_not_a_false_positive(session):
    result = await session.run(
        "echo fake; printf '%s' '__NOVADONE_00000000__:0|/bogus'", timeout=5
    )
    assert result["returncode"] == 0
    assert result["output"] == "fake\n__NOVADONE_00000000__:0|/bogus"


@pytest.mark.asyncio
async def test_concurrent_runs_are_serialized(session):
    results = await asyncio.gather(
        session.run("sleep 0.5; echo first"),
        session.run("echo second"),
    )
    assert results[0]["output"] == "first"
    assert results[1]["output"] == "second"


@pytest.mark.asyncio
async def test_trailing_ampersand_rejected(session):
    with pytest.raises(ValueError, match="trailing '&'"):
        await session.run("sleep 5 &")


@pytest.mark.asyncio
async def test_timeout_interrupts_and_session_survives(session):
    with pytest.raises(ValueError, match="timed out"):
        await session.run("sleep 30", timeout=1)
    result = await session.run("echo alive")
    assert result["output"] == "alive"


@pytest.mark.asyncio
async def test_password_prompt_blocked(session):
    # A command that asks like sudo does, so the test doesn't depend on the
    # machine's sudo setup (CI runners have passwordless sudo).
    with pytest.raises(ValueError, match="password prompt"):
        await session.run("printf 'Password: '; read -r secret", timeout=5)
    result = await session.run("echo after")
    assert result["output"] == "after"


def _sudo_asks_for_password() -> bool:
    import shutil
    import subprocess

    if shutil.which("sudo") is None:
        return False
    # -n fails instead of prompting: success means no password is needed.
    return subprocess.run(["sudo", "-k", "-n", "true"], capture_output=True).returncode != 0


@pytest.mark.asyncio
@pytest.mark.skipif(not _sudo_asks_for_password(), reason="sudo is absent or passwordless")
async def test_real_sudo_password_prompt_blocked(session):
    with pytest.raises(ValueError, match="password prompt"):
        await session.run("sudo -k true", timeout=5)
    result = await session.run("echo after")
    assert result["output"] == "after"


@pytest.mark.asyncio
async def test_reset_respawns_and_loses_state(session, tmp_path):
    # A directory unique to this test: the shell can't start in it, wherever
    # pytest itself was launched from.
    await session.run(f"cd {tmp_path}")
    session.reset()
    result = await session.run("pwd")
    assert result["cwd"] != str(tmp_path)


@pytest.mark.asyncio
async def test_close_is_idempotent(session):
    session.close()
    session.close()
    assert session.alive is False


def test_status_reflects_lifecycle():
    s = ShellSession(shell="/bin/bash")
    assert s.status()["alive"] is False
    s.close()


@pytest.mark.asyncio
async def test_write_input_reaches_shell(session):
    await session.ensure_started()
    queue = session.subscribe()
    collected = b""
    session.write_input("echo RAW_INPUT\n")
    for _ in range(100):
        while queue.qsize():
            collected += queue.get_nowait()
        if b"RAW_INPUT" in collected:
            break
        await asyncio.sleep(0.05)
    assert b"RAW_INPUT" in collected
    session.unsubscribe(queue)


@pytest.mark.asyncio
async def test_resize_changes_terminal_size(session):
    await session.ensure_started()
    session.resize(111, 23)
    result = await session.run("stty size")
    assert result["output"].strip() == "23 111"


@pytest.mark.asyncio
async def test_zsh_input_echo_is_stripped_from_output():
    """zsh's line editor re-echoes the sent command on the PTY; that echo
    must not leak into the agent-visible output."""
    zsh = shutil.which("zsh")
    if zsh is None:
        pytest.skip("zsh not installed")
    s = ShellSession(shell=zsh)
    try:
        await s.ensure_started()
        result = await s.run("echo ZLE_CLEAN")
        assert result["returncode"] == 0
        assert "ZLE_CLEAN" in result["output"]
        assert s._marker not in result["output"]
        assert "__nova_" not in result["output"]
    finally:
        s.close()


@pytest.mark.asyncio
async def test_subscribe_replays_buffer(session):
    await session.run("echo tagged")
    queue = session.subscribe()
    replayed = b""
    while queue.qsize():
        replayed += queue.get_nowait()
    assert b"tagged" in replayed
    session.unsubscribe(queue)


# ---- Prompt and output windowing ----------------------------------------


def _shells():
    found = [("/bin/bash", "bash")]
    zsh = shutil.which("zsh")
    if zsh:
        found.append((zsh, "zsh"))
    return found


def test_prompt_token_is_invisible():
    """The parsing token rides in an OSC title escape, not visible text."""
    s = ShellSession(shell="/bin/bash")
    assert s._prompt.startswith("\x1b]0;") and s._prompt.endswith("\x07")


@pytest.mark.asyncio
@pytest.mark.parametrize("shell", [path for path, _ in _shells()], ids=[n for _, n in _shells()])
async def test_long_command_that_wraps_is_clean(shell):
    """A command wider than the PTY is re-echoed across several lines by the
    line editor; none of that may reach the agent-visible output."""
    s = ShellSession(shell=shell)
    try:
        long_cmd = "echo " + "x" * 150
        result = await s.run(long_cmd)
        assert result["output"] == "x" * 150
    finally:
        s.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("shell", [path for path, _ in _shells()], ids=[n for _, n in _shells()])
async def test_exit_status_survives_between_runs(shell):
    s = ShellSession(shell=shell)
    try:
        assert (await s.run("false"))["returncode"] == 1
        assert (await s.run("echo $?"))["output"] == "1"
    finally:
        s.close()


@pytest.mark.asyncio
async def test_starship_multiline_prompt_never_leaks(tmp_path):
    zsh = shutil.which("zsh")
    if zsh is None or shutil.which("starship") is None:
        pytest.skip("needs zsh and starship")
    config = tmp_path / "starship.toml"
    config.write_text('add_newline = false\nformat = """$directory\n$character"""\n')
    s = ShellSession(shell=zsh, starship_config=config)
    try:
        for cmd, want in [("echo one", "one"), ("cd /", ""), ("pwd", "/"), ("echo " + "y" * 120, "y" * 120)]:
            assert (await s.run(cmd))["output"] == want
    finally:
        s.close()


# ---- Terminal view (what the terminal panel shows) ---------------------


def test_terminal_view_hides_sentinel_lines():
    s = ShellSession(shell="/bin/bash")
    view = TerminalView(s)
    stream = (
        b"$ ls\r\n" + s._start.encode() + b"\r\nfile_a\r\n"
        + s._marker.encode() + b":0|/tmp\r\n$ "
    )
    assert view.feed(stream) + view.flush() == b"$ ls\r\nfile_a\r\n$ "


def test_terminal_view_handles_sentinels_split_across_chunks():
    s = ShellSession(shell="/bin/bash")
    stream = (
        b"out\r\n" + s._start.encode() + b"\r\nmid\r\n"
        + s._marker.encode() + b":1|/home/x\r\nend"
    )
    for size in (1, 3, 7, 16):
        view = TerminalView(s)
        shown = b"".join(view.feed(stream[i:i + size]) for i in range(0, len(stream), size))
        assert shown + view.flush() == b"out\r\nmid\r\nend"


def test_terminal_view_flush_releases_lookalike_prefix():
    """A typed '_' (a possible sentinel start) must not stay hidden."""
    view = TerminalView(ShellSession(shell="/bin/bash"))
    assert view.feed(b"echo a_") == b"echo a"
    assert view.flush() == b"_"


@pytest.mark.asyncio
@pytest.mark.parametrize("shell", [path for path, _ in _shells()], ids=[n for _, n in _shells()])
async def test_terminal_replay_skips_setup_and_markers(shell):
    s = ShellSession(shell=shell)
    try:
        await s.run("echo VISIBLE_OUTPUT")
        await asyncio.sleep(0.1)
        queue = s.subscribe()
        view = TerminalView(s)
        shown = b""
        while queue.qsize():
            shown += view.feed(queue.get_nowait())
        shown += view.flush()
        assert b"VISIBLE_OUTPUT" in shown
        assert b"__NOVASTART" not in shown and b"__NOVADONE" not in shown
        assert b"PROMPT_COMMAND" not in shown and b"add-zsh-hook" not in shown
    finally:
        s.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("shell", [path for path, _ in _shells()], ids=[n for _, n in _shells()])
async def test_trailing_comment_does_not_hang(shell):
    s = ShellSession(shell=shell)
    try:
        result = await asyncio.wait_for(s.run("echo hi # explain"), 10)
        assert result["output"] == "hi"
    finally:
        s.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("shell", [path for path, _ in _shells()], ids=[n for _, n in _shells()])
async def test_user_typed_command_does_not_complete_next_run(shell):
    """Commands typed in the terminal also print markers; a later run must
    only accept the marker of its own command."""
    s = ShellSession(shell=shell)
    try:
        await s.ensure_started()
        s.write_input("echo FROM_USER\n")
        await asyncio.sleep(0.5)
        result = await s.run("echo FROM_NOVA")
        assert result["output"] == "FROM_NOVA"
    finally:
        s.close()


# ---- Tool registration & registry execution ---------------------------


def test_build_registers_three_tools():
    from nova.tools.shell import build

    tools = build(ShellSession(shell="/bin/bash"))
    assert [t.name for t in tools] == [
        "shell.execute",
        "shell.session_info",
        "shell.reset",
    ]
    by_name = {t.name: t for t in tools}
    assert by_name["shell.execute"].risk == RiskLevel.WRITE
    assert by_name["shell.execute"].permission == "shell.execute"
    assert by_name["shell.session_info"].risk == RiskLevel.READ
    assert by_name["shell.reset"].risk == RiskLevel.WRITE
    # Shell is never covered by the blanket "auto-approve WRITE" setting.
    assert by_name["shell.execute"].auto_approvable is False
    assert by_name["shell.reset"].auto_approvable is False


@pytest.mark.asyncio
async def test_registry_execute_persists_state(session):
    registry = ToolRegistry()
    registry.register_shell_tools(session)
    registry.permissions.grant("shell.execute")

    run = await registry.execute("shell.execute", {"command": "cd /tmp; export P=1"})
    assert run.success is True
    run = await registry.execute("shell.execute", {"command": "pwd; echo $P"})
    assert run.success is True
    assert run.data["output"] == "/tmp\n1"


@pytest.mark.asyncio
async def test_registry_read_tool_needs_no_grant(session):
    registry = ToolRegistry()
    registry.register_shell_tools(session)
    info = await registry.execute("shell.session_info")
    assert info.success is True
    assert info.data["shell"] == "/bin/bash"


@pytest.mark.asyncio
async def test_registry_reset_tool(session, tmp_path):
    registry = ToolRegistry()
    registry.register_shell_tools(session)
    registry.permissions.grant("shell.execute")

    await registry.execute("shell.execute", {"command": f"cd {tmp_path}"})
    result = await registry.execute("shell.reset")
    assert result.success is True
    assert result.data["alive"] is True

    run = await registry.execute("shell.execute", {"command": "pwd"})
    assert run.data["cwd"] != str(tmp_path)


@pytest.mark.asyncio
async def test_registry_denies_shell_execute_without_grant(session):
    registry = ToolRegistry()
    registry.register_shell_tools(session)
    result = await registry.execute("shell.execute", {"command": "echo hi"})
    assert result.success is False
    assert "permission" in result.error.lower()