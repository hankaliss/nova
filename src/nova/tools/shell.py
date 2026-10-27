"""Shell session tools for Nova, bound to a persistent ShellSession.

Commands run in a single long-lived interactive shell (e.g. bash), so ``cwd``,
exported variables and running jobs persist between calls — a follow-up like
"now look inside that directory" works naturally across agent turns.

Unlike the old one-shot ``shell.execute`` (a fresh ``subprocess.run(shell=True)``
per call), this tool has no ``shell.resume``-style tricks: the session *is*
the shell.
"""

from typing import Any

from nova.shell.session import ShellSession
from nova.tools.tool import RiskLevel, Tool


def build(session: ShellSession) -> list[Tool]:
    """Build the shell tools bound to ``session``."""

    async def execute(command: str) -> dict[str, Any]:
        return await session.run(command)

    async def session_info() -> dict[str, Any]:
        return session.status()

    async def reset() -> dict[str, Any]:
        session.reset()
        return session.status()

    return [
        Tool(
            name="shell.execute",
            description=(
                "Run a shell command in the shared persistent session "
                "(cwd, environment and running jobs persist between calls). "
                "Returns {output, returncode, cwd}. Commands that need "
                "elevation must use `sudo -n` (password prompts are blocked). "
                "Use this for systemctl, ps, df, git, pacman, etc."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": (
                            "The shell command to execute. May change cwd or "
                            "export variables; both persist."
                        ),
                    },
                },
                "required": ["command"],
            },
            risk=RiskLevel.WRITE,
            permission="shell.execute",
            auto_approvable=False,
            implementation=execute,
        ),
        Tool(
            name="shell.session_info",
            description=(
                "Show the state of the shared shell session: alive flag, shell "
                "path, current working directory and how long it has run."
            ),
            parameters={
                "type": "object",
                "properties": {},
            },
            risk=RiskLevel.READ,
            permission="system.info",
            implementation=session_info,
        ),
        Tool(
            name="shell.reset",
            description=(
                "Kill and respawn the shared shell session from scratch. Use "
                "when a command left the shell stuck or in a bad state; all "
                "session cwd/variables are lost."
            ),
            parameters={
                "type": "object",
                "properties": {},
            },
            risk=RiskLevel.WRITE,
            permission="shell.execute",
            auto_approvable=False,
            implementation=reset,
        ),
    ]