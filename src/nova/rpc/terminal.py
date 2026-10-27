"""Embedded terminal (AD-022): a view on the shared shell session."""

import asyncio


class TerminalMixin:
    """Part of :class:`nova.rpc.core.NovaRpc`; state lives on the instance."""

    async def terminalOpen(self) -> None:
        """Start streaming the shared shell session to the renderer."""
        if self._shell_session is None:
            raise RuntimeError("Shell session unavailable")
        if self._term_task is not None and not self._term_task.done():
            return
        # Make sure a live shell exists so the panel shows a real prompt.
        await self._shell_session.ensure_started()
        self._term_queue = self._shell_session.subscribe()
        self._term_task = asyncio.get_running_loop().create_task(
            self._terminal_pump()
        )

    def terminalClose(self) -> None:
        """Stop streaming terminal output (the session keeps running)."""
        if self._term_task is not None and not self._term_task.done():
            self._term_task.cancel()
        self._term_task = None
        if self._shell_session is not None and self._term_queue is not None:
            self._shell_session.unsubscribe(self._term_queue)
        self._term_queue = None

    def terminalInput(self, data: str) -> None:
        """Forward raw keystrokes from the terminal panel to the session."""
        if self._shell_session is None:
            raise RuntimeError("Shell session unavailable")
        self._shell_session.write_input(data)

    def terminalResize(self, cols: int, rows: int) -> None:
        """Forward a terminal size change (TIOCSWINSZ on the PTY)."""
        if self._shell_session is None:
            raise RuntimeError("Shell session unavailable")
        self._shell_session.resize(cols, rows)

    async def _terminal_pump(self) -> None:
        """Emit the live session output as base64 `term_output` events."""
        import base64

        from nova.shell.session import TerminalView

        queue = self._term_queue
        view = TerminalView(self._shell_session)
        while queue is not None:
            try:
                # A short wait lets held-back bytes (a possible split sentinel)
                # go out promptly when nothing else arrives.
                chunk = await asyncio.wait_for(queue.get(), timeout=0.05)
                data = view.feed(chunk)
            except TimeoutError:
                data = view.flush()
            except asyncio.CancelledError:
                return
            if data:
                self.emit(
                    "term_output",
                    base64.b64encode(data).decode("ascii"),
                )
