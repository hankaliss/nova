"""A single MCP server connection over stdio.

The official SDK's ``stdio_client`` is an async context manager that
owns the subprocess. We hold it open inside a long-lived asyncio task
and park there until the client is stopped or the process dies. A cheap
periodic ``tools/list`` acts as a health check so silent crashes are
noticed and restarted with exponential backoff.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading
from typing import Any, Callable

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import get_default_environment, stdio_client

from nova.mcp.config import McpServerConfig

logger = logging.getLogger(__name__)

INIT_TIMEOUT_S = 15.0
LIST_TIMEOUT_S = 15.0
HEALTH_INTERVAL_S = 10.0

# Substrings (case-insensitive) in a server's stderr output that report the
# browser-based login has finished. github-mcp-server logs e.g.
#   msg="github authorization complete"
_AUTH_COMPLETE_MARKERS = ("authorization complete",)


class _StderrTap:
    """Server stderr → background reader thread.

    The SDK forwards ``errlog`` directly to ``anyio.open_process``, which
    needs a real file descriptor — so we hand it the write end of an
    ``os.pipe`` and pump the read end in a daemon thread: every byte is
    still forwarded to Nova's own stderr (logging unchanged), and lines are
    scanned for authorization-completion markers.
    """

    def __init__(self) -> None:
        self._read_fd, self._write_fd = os.pipe()
        self.on_line: Callable[[str], None] | None = None

    def fileno(self) -> int:
        return self._write_fd

    def start(self) -> None:
        threading.Thread(target=self._pump, name="mcp-stderr-tap", daemon=True).start()

    def close(self) -> None:
        """Close the write end — only after the subprocess has exited."""
        try:
            os.close(self._write_fd)
        except OSError:
            pass

    def _pump(self) -> None:
        buf = b""
        with os.fdopen(self._read_fd, "rb", buffering=0) as reader:
            while True:
                try:
                    chunk = reader.read(4096)
                except OSError:
                    break
                if not chunk:
                    break
                try:
                    sys.stderr.buffer.write(chunk)
                    sys.stderr.flush()
                except Exception:
                    pass
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if self.on_line is None:
                        continue
                    try:
                        self.on_line(line.decode("utf-8", "replace"))
                    except Exception:
                        logger.exception("mcp stderr tap callback failed")

# disabled → starting → connected → failed → (backoff) → starting …
State = str


def _unwrap(e: BaseException) -> str:
    """anyio wraps subprocess errors in an ExceptionGroup; show the root."""
    if isinstance(e, BaseExceptionGroup) and len(e.exceptions) == 1:
        return str(e.exceptions[0])
    return str(e)


class McpClient:
    """Owns one MCP server subprocess and its session."""

    def __init__(
        self,
        name: str,
        config: McpServerConfig,
        on_state_change: Callable[["McpClient"], None] | None = None,
    ) -> None:
        self.name = name
        self.config = config
        self.on_state_change = on_state_change
        self.state: State = "disabled"
        self.error: str = ""
        self.tools: list[Any] = []  # mcp.types.Tool from the last listing
        # Pending login request ({"url": ..., "code": ...}) when the server
        # answered a tool call with an authorization challenge; None otherwise.
        self.auth: dict[str, str] | None = None
        # Monotonic timestamp of the last real server call made while auth
        # was pending (probe gate — see McpManager).
        self.auth_probed_at: float = 0.0
        # Signaled when auth completes (either by stderr tap or user click).
        # Tool implementations wait on this instead of raising immediately.
        self.auth_event: asyncio.Event = asyncio.Event()

        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._session: ClientSession | None = None
        self._call_lock = asyncio.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    # ---- Lifecycle ------------------------------------------------------

    @property
    def connected(self) -> bool:
        return self.state == "connected" and self._session is not None

    def status(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "enabled": self.config.enabled,
            "state": self.state,
            "error": self.error,
            "command": self.config.command,
            "toolCount": len(self.tools),
            "auth": self.auth,
        }

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self.config.enabled = True
        self.error = ""
        self._stop.clear()
        self._set_state("starting")
        self._task = asyncio.create_task(self._run(), name=f"mcp-{self.name}")

    async def stop(self) -> None:
        self.config.enabled = False
        await self._halt()

    async def restart(self) -> None:
        await self._halt()
        await self.start()

    async def _halt(self) -> None:
        self._stop.set()
        task = self._task
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._task = None
        self._session = None
        self.tools = []
        self.auth = None
        self._set_state("disabled")

    async def shutdown(self) -> None:
        """Stop for good (app teardown)."""
        self._stop.set()
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._session = None
        self.tools = []
        self.auth = None

    # ---- Runtime --------------------------------------------------------

    def _set_state(self, state: str, error: str = "") -> None:
        changed = self.state != state or self.error != error
        self.state = state
        self.error = error
        if changed and self.on_state_change is not None:
            try:
                self.on_state_change(self)
            except Exception:  # never let UI callbacks kill the client
                logger.exception("mcp state callback failed")

    def _server_params(self) -> StdioServerParameters:
        env = get_default_environment()
        env.update(self.config.env)
        return StdioServerParameters(
            command=self.config.command,
            args=self.config.args,
            env=env,
            cwd=self.config.cwd,
        )

    async def _run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            self._set_state("starting")
            self._loop = asyncio.get_running_loop()
            tap = _StderrTap()
            tap.on_line = self._handle_stderr_line
            tap.start()
            try:
                async with stdio_client(self._server_params(), errlog=tap) as (read, write):
                    async with ClientSession(read, write) as session:
                        await asyncio.wait_for(session.initialize(), INIT_TIMEOUT_S)
                        listed = await session.list_tools()
                        async with self._call_lock:
                            self._session = session
                        self.tools = list(listed.tools)
                        self._set_state("connected")
                        backoff = 1.0
                        await self._monitor(session)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                async with self._call_lock:
                    self._session = None
                    self.tools = []
                self._set_state("failed", _unwrap(e))
                if self._stop.is_set():
                    return
                logger.warning("mcp/%s failed: %s — restarting in %.0fs", self.name, e, backoff)
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=backoff)
                    return  # stopped while backing off
                except TimeoutError:
                    pass
                backoff = min(backoff * 2, 30.0)
            finally:
                tap.close()

    async def _monitor(self, session: ClientSession) -> None:
        """Park until stopped; ping periodically to detect dead servers."""
        while True:
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=HEALTH_INTERVAL_S)
                return  # stop requested
            except TimeoutError:
                pass
            try:
                await asyncio.wait_for(session.list_tools(), timeout=LIST_TIMEOUT_S)
            except Exception as e:
                raise RuntimeError(f"health check failed: {e}") from e

    # ---- Auth completion hint -------------------------------------------

    def _handle_stderr_line(self, line: str) -> None:
        """Runs on the stderr tap thread."""
        if not any(marker in line.lower() for marker in _AUTH_COMPLETE_MARKERS):
            return
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(self._release_auth)
        except RuntimeError:
            pass  # loop shutting down

    def _release_auth(self) -> None:
        """The server reports the browser login finished — drop the pending
        challenge so the next tool call goes straight through (no probe
        interval, no button press needed)."""
        if self.auth is None:
            return
        logger.info("mcp/%s: server reports authorization complete", self.name)
        self.auth = None
        self.auth_probed_at = 0.0
        self.auth_event.set()
        if self.on_state_change is not None:
            try:
                self.on_state_change(self)
            except Exception:
                logger.exception("mcp/%s auth-release callback failed", self.name)

    # ---- Calls ----------------------------------------------------------

    def _require_session(self) -> ClientSession:
        session = self._session
        if session is None or not self.connected:
            raise RuntimeError(f"MCP server '{self.name}' is not connected")
        return session

    async def call_tool(self, tool_name: str, args: dict[str, Any]) -> Any:
        """Execute a tool on this server. Raises on unavailability/error."""
        session = self._require_session()
        async with self._call_lock:
            return await asyncio.wait_for(
                session.call_tool(tool_name, args), timeout=self.config.timeout_s
            )
