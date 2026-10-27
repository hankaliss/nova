"""Persistent interactive shell session for Nova.

One long-lived shell (the user's ``$SHELL``, ``--norc``/``--noprofile``) is
attached to a pseudo-terminal and shared by the LLM's ``shell.execute`` tool
and — later — the embedded terminal panel. Because commands run in the same
live process, ``cwd`` and exported variables persist between prompts, so a
follow-up like "now look inside that directory" works naturally.

Execution model
---------------

The shell runs interactively on a PTY with:

* terminal echo disabled (agent-driven commands don't echo back),
* a prompt that ends in an invisible session token: the token rides in an
  OSC 0 (window-title) escape, which terminals consume without drawing, so
  the visible prompt can be anything (built-in ``AI/nova ❯``, or starship
  with a given config) while a stray prompt after a result is still a known,
  strippable byte string (everything up to the token is dropped),
* a completion marker appended after each command::

      <command> ; echo "<marker>:$?|$(pwd)"

  ``run()`` records the buffer position *before* the command, waits until the
  real ``<marker>:«rc»|«cwd»`` line appears, and returns everything between
  those two points. The ``$(pwd)`` component keeps ``cwd`` accurate without a
  separate call, and the prompt token printed after the marker is trimmed from
  the next window.

Guards
------

* **Timeout** — a command that never returns (interactive app, stray heredoc)
  is interrupted with SIGINT and reported as an error; `reset()` respawns the
  session if it ends up stuck.
* **Password prompts** — if sudo (or anything else) begins a password prompt,
  ``run()`` aborts immediately and tells the model to use ``sudo -n``. The
  agent must never see or supply a password.
* **Trailing ``&``** — a background-amperstand command would swallow the
  completion marker; it is rejected up front (`(cmd &)` is the supported form).

The class is pure stdlib (``os.openpty``, ``asyncio``).
"""

from __future__ import annotations

import asyncio
import fcntl
import os
import pty
import re
import secrets
import signal
import struct
import subprocess
import termios
import time
from pathlib import Path
from typing import Any

# Prompt a password guard pattern matches. We never echo a response; the
# guard just aborts the run and reports the failure.
_PASSWORD_PROMPT_RE = re.compile(r"(\[sudo\] password for|password for|Password:|Enter PIN:)")

_BUFFER_CAP = 200_000  # bytes kept in the ring buffer


def _find_shell() -> str:
    return os.environ.get("SHELL") or "/bin/bash"


class ShellSession:
    """A single interactive shell attached to a PTY, addressable across
    agent turns and (future) terminal subscribers."""

    def __init__(
        self,
        shell: str | None = None,
        spawn_timeout: float = 10.0,
        starship_config: Path | None = None,
    ) -> None:
        self.shell = shell or _find_shell()
        self._spawn_timeout = spawn_timeout
        # Only zsh loads starship (its bash init rewrites PS1 every prompt).
        self._starship_config = starship_config
        self._token = secrets.token_hex(4)
        self._marker = f"__NOVADONE_{self._token}__"
        # Printed right before each command runs (zsh preexec / bash PS0), so
        # the shell's echo of the typed line never contains it.
        self._start = f"__NOVASTART_{self._token}__"
        # Invisible end-of-prompt token: OSC 0 (set title) ... BEL.
        self._prompt = f"\x1b]0;__NOVAPS1_{self._token}__\x07"
        self._marker_re = re.compile(
            re.escape(self._marker).encode() + rb":(-?\d+)\|(.+)"
        )

        self._proc: subprocess.Popen | None = None
        self._master_fd: int | None = None
        self._pump_task: asyncio.Task | None = None
        self._buffer = bytearray()
        self._buffer_floor = 0  # bytes up to here are consumed; runs start here
        self._total = 0  # bytes ever appended (absolute offsets survive trimming)
        self._display_from = 0  # absolute offset the terminal view replays from
        self._primed = False
        self._dead = False
        self._exit_code: int | None = None
        self._changed = asyncio.Event()
        self._lock = asyncio.Lock()
        self._subscribers: list[asyncio.Queue[bytes]] = []
        self.started_at = 0.0
        self.last_cwd: str | None = None

    # ---- Lifecycle ------------------------------------------------------

    @property
    def alive(self) -> bool:
        proc = self._proc
        return proc is not None and proc.poll() is None and not self._dead

    def _spawn(self) -> None:
        if self.alive:
            return
        name = os.path.basename(self.shell)
        argv = [self.shell]
        if name in ("bash", "sh"):
            # Long options must precede the short "-i": bash's option parser
            # chokes on a long option that follows "-i".
            argv += ["--norc", "--noprofile", "-i"]
        elif name == "zsh":
            argv += ["--no-rcs", "-i"]
        else:
            argv += ["-i"]

        master, slave = pty.openpty()
        termios.tcsetattr(slave, termios.TCSANOW, _disable_echo(termios.tcgetattr(slave)))
        _set_winsize(slave, cols=80, rows=24)

        def child_setup(_fd: int = slave) -> None:
            os.setsid()
            fcntl.ioctl(_fd, termios.TIOCSCTTY, 0)

        try:
            self._proc = subprocess.Popen(
                argv,
                stdin=slave,
                stdout=slave,
                stderr=slave,
                preexec_fn=child_setup,
                close_fds=True,
            )
            self._master_fd = master
            os.set_blocking(master, False)
            self.started_at = time.monotonic()
            self._primed = False
            self._dead = False
            self._pump_task = asyncio.create_task(self._pump())
        finally:
            os.close(slave)

    async def _ensure_started(self) -> None:
        """Spawn the shell on demand and prime it (clear PS1, verify alive)."""
        self._spawn()
        if self._primed:
            return
        start = self._buffer_floor
        hooks = self._hooks_setup()
        self._write(
            f"{self._prompt_setup()}; {hooks}{'; ' if hooks else ''}"
            f"echo \"{self._marker}:$?|$(pwd)\"\n"
        )
        try:
            await asyncio.wait_for(self._wait_for_marker(start), self._spawn_timeout)
            # The terminal view starts after the setup line's output...
            match = self._marker_re.search(bytes(self._buffer[start:]))
            end = start + (match.end() if match else 0)
            nl = self._buffer.find(b"\n", end)
            self._display_from = self._abs(nl + 1 if nl >= 0 else len(self._buffer))
            # ...and runs start after the first real prompt, so the marker the
            # precmd hook prints before it can't complete the first run.
            prompt = self._prompt.encode()
            await asyncio.wait_for(self._wait_for_bytes(prompt, end), self._spawn_timeout)
        except (TimeoutError, RuntimeError) as e:
            self.close()
            raise RuntimeError(f"Shell failed to start: {e}") from e
        self._primed = True
        self._buffer_floor = len(self._buffer)

    def _abs(self, index: int) -> int:
        """Buffer index → absolute stream offset."""
        return self._total - len(self._buffer) + index

    async def _wait_for_bytes(self, needle: bytes, start: int) -> None:
        while self._buffer.find(needle, start) < 0:
            if self._dead:
                raise RuntimeError(f"shell exited (rc={self._exit_code})")
            self._changed.clear()
            if self._buffer.find(needle, start) >= 0:
                return
            await self._changed.wait()

    def _hooks_setup(self) -> str:
        """Shell hooks that bracket every command with Nova's sentinels.

        Nova types only the bare command; the shell prints the start sentinel
        just before running it (zsh ``preexec`` / bash ``PS0``) and the
        completion marker with ``$?`` and ``$PWD`` just before the next prompt
        (zsh ``precmd`` / bash ``PROMPT_COMMAND``), passing ``$?`` through.
        The terminal shows exactly what was typed; ``TerminalView`` hides the
        two sentinel lines. Other shells get no hooks (see ``_typed_line``).
        """
        post = (
            f"__nova_post() {{ local r=$?; "
            f"printf '%s\\n' \"{self._marker}:$r|$PWD\"; return $r; }}"
        )
        name = os.path.basename(self.shell)
        if name == "zsh":
            return (
                f"__nova_pre() {{ printf '%s\\n' '{self._start}'; }}; {post}; "
                # zsh draws its no-newline mark ("%") before precmd, i.e.
                # inside the run's output; keep the behaviour, drop the mark.
                "PROMPT_EOL_MARK=''; setopt interactive_comments; "
                "autoload -Uz add-zsh-hook; "
                "add-zsh-hook preexec __nova_pre; add-zsh-hook precmd __nova_post"
            )
        if name == "bash":
            return f"{post}; PROMPT_COMMAND=__nova_post; PS0='{self._start}\\n'"
        return ""

    def _typed_line(self, command: str) -> str:
        """What Nova types for ``command``: bare with hooks, else wrapped."""
        if os.path.basename(self.shell) in ("zsh", "bash"):
            return f"{command}\n"
        return (
            f"printf '%s\\n' '{self._start}'; {command} ; "
            f"echo \"{self._marker}:$?|$(pwd)\"\n"
        )

    def _prompt_setup(self) -> str:
        """Shell code that installs the visible prompt + invisible token."""
        title = f"__NOVAPS1_{self._token}__"
        name = os.path.basename(self.shell)
        if name == "zsh":
            token = f"%{{\\e]0;{title}\\a%}}"  # zero-width in zsh
            config = self._starship_config
            if config is not None and config.is_file():
                # Prompt only — the rest of the user's .zshrc stays unloaded
                # so aliases/plugins never change what the model sees. The
                # right prompt would be drawn after the token, so drop it.
                return (
                    f"if command -v starship >/dev/null; then "
                    f"export STARSHIP_CONFIG='{config}'; "
                    f'eval "$(starship init zsh)"; RPROMPT=""; '
                    f"PROMPT=\"$PROMPT\"$'{token}'; "
                    f"else PROMPT=$'%2~ ❯ {token}'; fi"
                )
            return f"PROMPT=$'%2~ ❯ {token}'"
        # bash/sh: \[ \] mark the escape as zero-width
        return f"PS1='\\W ❯ \\[\\e]0;{title}\\a\\]'"

    def close(self) -> None:
        """Kill the shell, drop the PTY and cancel the pump (idempotent)."""
        self._dead = True
        self._changed.set()
        if self._pump_task is not None and not self._pump_task.done():
            self._pump_task.cancel()
        if self._proc is not None:
            if self._proc.poll() is None:
                try:
                    os.killpg(self._proc.pid, signal.SIGTERM)
                except OSError:
                    try:
                        self._proc.terminate()
                    except Exception:
                        pass
                try:
                    self._proc.wait(timeout=2)
                except Exception:
                    try:
                        os.killpg(self._proc.pid, signal.SIGKILL)
                    except OSError:
                        pass
            self._proc = None
        if self._master_fd is not None:
            try:
                os.close(self._master_fd)
            except OSError:
                pass
            self._master_fd = None

    def reset(self) -> None:
        """Respawn the shell from scratch, losing session state."""
        self.close()
        self._buffer.clear()
        self._buffer_floor = 0
        self.last_cwd = None
        self._spawn()
        self._primed = False

    # ---- Pump: master fd -> ring buffer + subscribers -------------------

    async def _pump(self) -> None:
        """Read everything the shell writes until EOF or the fd is closed."""
        fd = self._master_fd
        while fd is not None and not self._dead:
            await asyncio.sleep(0.01)
            try:
                self._read_available(fd)
            except OSError:
                self._dead = True
                self._exit_code = self._proc.poll() if self._proc else None
                self._changed.set()
                return

    def _read_available(self, fd: int) -> None:
        while True:
            try:
                chunk = os.read(fd, 65536)
            except (BlockingIOError, InterruptedError):
                return
            except OSError:
                raise
            if not chunk:
                self._dead = True
                self._exit_code = self._proc.poll() if self._proc else None
                self._changed.set()
                return
            self._append(chunk)

    def _append(self, chunk: bytes) -> None:
        self._total += len(chunk)
        if len(self._buffer) >= _BUFFER_CAP:
            del self._buffer[: max(1, len(self._buffer) - _BUFFER_CAP + len(chunk))]
        self._buffer.extend(chunk)
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(chunk)
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    queue.put_nowait(chunk)
                except asyncio.QueueFull:
                    pass
        self._changed.set()

    # ---- Subscribers (terminal panel, Phase 2) --------------------------

    def subscribe(self) -> asyncio.Queue[bytes]:
        queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=256)
        self._subscribers.append(queue)
        # Replay from the end of the setup line (not the shell's priming).
        first = max(0, self._display_from - (self._total - len(self._buffer)))
        replay = bytes(self._buffer[first:])
        if replay:
            try:
                queue.put_nowait(replay)
            except asyncio.QueueFull:
                pass
        return queue

    def unsubscribe(self, queue: asyncio.Queue[bytes]) -> None:
        if queue in self._subscribers:
            self._subscribers.remove(queue)

    def tail(self, limit: int = _BUFFER_CAP) -> bytes:
        """The most recent bytes from the session (ring buffer)."""
        return bytes(self._buffer[-limit:])

    # ---- Command execution ---------------------------------------------

    def _write(self, text: str) -> None:
        if self._master_fd is None:
            raise RuntimeError("shell not started")
        os.write(self._master_fd, text.encode("utf-8", errors="replace"))

    def write_input(self, data: str) -> None:
        """Send raw terminal input (keystrokes) to the foreground process.

        Usable from the terminal panel while the shell is idle or mid-command
        (this is how Ctrl+C interrupts an LLM-run command).
        """
        if self._master_fd is None:
            raise RuntimeError("shell not started")
        os.write(self._master_fd, data.encode("utf-8", errors="replace"))

    def resize(self, cols: int, rows: int) -> None:
        """Update the pseudo-terminal window size (TIOCSWINSZ)."""
        if self._master_fd is None:
            raise RuntimeError("shell not started")
        _set_winsize(self._master_fd, cols=int(cols), rows=int(rows))

    async def ensure_started(self) -> None:
        """Spawn + prime the shell so a terminal can attach to a live prompt."""
        await self._ensure_started()

    def interrupt(self) -> None:
        """Send SIGINT to the foreground process (Ctrl+C on the PTY)."""
        if self._master_fd is not None:
            self._write("\x03")

    async def _settle_after_abort(self) -> None:
        """Wait briefly for the shell to return to a fresh prompt after an
        interrupt, then advance the consumption floor past the abort noise.

        SIGINT'd commands often print a final error line (e.g. sudo) before
        the next prompt; without this, that line would leak into the *next*
        run's window. If the shell does not recover quickly (a raw-mode
        password read can swallow the interrupt entirely), hard-reset so the
        next command runs in a guaranteed-clean shell.
        """
        prompt = self._prompt.encode()
        for _ in range(150):  # ~1.5s grace
            if self._dead:
                self._buffer_floor = len(self._buffer)
                return
            idx = self._buffer.find(prompt, self._buffer_floor)
            if idx >= 0:
                self._buffer_floor = idx + len(prompt)
                return
            await asyncio.sleep(0.01)
        self.reset()
        self._buffer_floor = 0
        self._primed = False

    async def _wait_for_marker(self, start: int) -> None:
        """Wait until a completion marker line appears after ``start``.

        Aborts early (with SIGINT) if the command prompts for a password.
        """
        while True:
            if self._dead:
                raise RuntimeError(
                    f"shell exited (rc={self._exit_code}) — use shell.reset"
                )
            window = bytes(self._buffer[start:])
            if _PASSWORD_PROMPT_RE.search(_decode(window)):
                self.interrupt()
                raise ValueError(
                    "The command produced a password prompt, which is blocked. "
                    "Never request a password interactively — use `sudo -n` so "
                    "non-interactive mode fails cleanly instead of hanging."
                )
            if self._marker_re.search(window):
                return
            self._changed.clear()
            if self._dead:
                raise RuntimeError(
                    f"shell exited (rc={self._exit_code}) — use shell.reset"
                )
            if self._marker_re.search(bytes(self._buffer[start:])):
                return
            await self._changed.wait()

    async def run(self, command: str, timeout: float = 120.0) -> dict[str, Any]:
        """Run ``command`` in the shared session.

        Returns ``{"output", "returncode", "cwd"}``. Raises ``ValueError`` for
        unsupported commands, timeouts and blocked password prompts.
        """
        command = command.strip()
        if not command:
            raise ValueError("empty command")
        if command.endswith("&"):
            raise ValueError(
                "trailing '&' is not supported in shell.execute — background "
                "commands must be wrapped, e.g. '(cmds &)'"
            )

        async with self._lock:
            await self._ensure_started()
            # Search from *now*: anything earlier (including markers from
            # commands the user typed in the terminal) is not this run's.
            start = max(self._buffer_floor, len(self._buffer))
            self._write(self._typed_line(command))
            try:
                await asyncio.wait_for(self._wait_for_marker(start), timeout)
            except Exception as e:
                # Poke the shell back to a prompt so the session stays usable
                # and skip the abort chatter (kill messages, error lines) so
                # the next run starts from a clean prompt.
                self.interrupt()
                await self._settle_after_abort()
                if isinstance(e, TimeoutError):
                    partial = _decode(bytes(self._buffer[start:]))
                    raise ValueError(
                        f"Command timed out after {timeout:.0f}s — partial output:\n"
                        f"{partial[:2000]}\n"
                        "The session may still be busy; run shell.reset (or "
                        "interrupt from the terminal) if it is stuck."
                    ) from e
                raise

            # Let the shell finish drawing its next prompt before returning:
            # input sent mid-redraw is echoed by the tty driver (a duplicate
            # line in the terminal) instead of the line editor.
            match = self._marker_re.search(bytes(self._buffer[start:]))
            if match is not None:
                try:
                    await asyncio.wait_for(
                        self._wait_for_bytes(self._prompt.encode(), start + match.end()),
                        2.0,
                    )
                except (TimeoutError, RuntimeError):
                    pass
            window = bytes(self._buffer[start:])
            output, returncode, cwd = self._parse_window(window)
            output = _clean_output(output)
            # Everything up to here (result + the prompt that follows it) is
            # consumed — new runs continue from the prompt, not mid-stream.
            self._buffer_floor = len(self._buffer)
            if returncode is None:
                raise RuntimeError(
                    f"shell exited (rc={self._exit_code}) — use shell.reset"
                )

            if self._dead:
                raise RuntimeError(
                    f"shell exited (rc={self._exit_code}) — use shell.reset"
                )

            if cwd:
                self.last_cwd = cwd.strip()
            return {
                "output": output,
                "returncode": returncode,
                "cwd": cwd.strip() if cwd else self.last_cwd,
            }

    def _parse_window(self, window: bytes) -> tuple[bytes, int | None, str | None]:
        """Split a run window into (output, returncode, cwd)."""
        match = self._marker_re.search(window)
        if match is None:
            return (window, None, None)
        split = match.group(0).split(b"|", 1)
        returncode = int(split[0].split(b":")[-1].decode(), 10)
        cwd = split[1].decode("utf-8", errors="replace") if len(split) == 2 else None
        head = window[: match.start()]
        start = head.find(self._start.encode())
        if start >= 0:
            # Everything before the start sentinel (prompt, the shell's echo
            # of the typed line however it wrapped) is not command output.
            nl = head.find(b"\n", start)
            output = head[nl + 1:] if nl >= 0 else b""
        else:
            # Fallback: prompt first, since a multi-line prompt (starship)
            # puts the echoed command on its last line.
            output = self._strip_leading_prompt(head)
            output = self._strip_input_echo(output)
        return (output, returncode, cwd)

    def _strip_input_echo(self, data: bytes) -> bytes:
        """Drop the interactive shell's echo of the command we just sent.

        zsh's line editor (ZLE) re-renders typed input on the PTY regardless
        of termios ECHO, so a run window starts with the echoed command line
        (with ``\\x08`` redraw artifacts) on shells without hooks, where the
        typed line carries the marker. Only a fallback: ``_parse_window``
        normally cuts at the start sentinel instead.
        """
        n = data.find(b"\n")
        if n < 0:
            line = data
        else:
            line = data[:n]
        if self._marker.encode() in line:
            return data[n + 1:] if n >= 0 else b""
        return data

    def _strip_leading_prompt(self, data: bytes) -> bytes:
        prompt = self._prompt.encode("utf-8")
        idx = data.find(prompt)
        if idx < 0:
            return data
        return data[idx + len(prompt):]

    def status(self) -> dict[str, Any]:
        """Session info for ``shell.session_info``."""
        return {
            "alive": self.alive,
            "shell": self.shell,
            "cwd": self.last_cwd,
            "since_ms": int((time.monotonic() - self.started_at) * 1000)
            if self.alive
            else 0,
            "buffer_bytes": len(self._buffer),
            "interruptible": self._master_fd is not None,
        }


class TerminalView:
    """Hides Nova's run sentinels from the terminal panel's byte stream.

    The shell prints a start line and a completion line around every command
    (see ``ShellSession._hooks_setup``); the model needs them, the person
    watching the terminal doesn't. Each sentinel is dropped through the end
    of its line. A chunk may end mid-sentinel, so a trailing partial match is
    held back until the next chunk, or until ``flush()`` after a pause (so a
    typed ``_`` never lags).
    """

    def __init__(self, session: "ShellSession") -> None:
        self._needles = [session._start.encode(), session._marker.encode()]
        self._pending = b""

    def feed(self, chunk: bytes) -> bytes:
        data = self._pending + chunk
        self._pending = b""
        out = bytearray()
        i = 0
        while True:
            hits = [p for p in (data.find(n, i) for n in self._needles) if p >= 0]
            if not hits:
                break
            at = min(hits)
            nl = data.find(b"\n", at)
            out += data[i:at]
            if nl < 0:  # sentinel line not complete yet
                self._pending = data[at:]
                return bytes(out)
            i = nl + 1
        rest = data[i:]
        keep = max((self._partial(rest, n) for n in self._needles), default=0)
        out += rest[: len(rest) - keep]
        self._pending = rest[len(rest) - keep:]
        return bytes(out)

    def flush(self) -> bytes:
        """Release held-back bytes that never became a sentinel."""
        data, self._pending = self._pending, b""
        return data

    @staticmethod
    def _partial(data: bytes, needle: bytes) -> int:
        """Length of the longest suffix of ``data`` that starts ``needle``."""
        for k in range(min(len(needle) - 1, len(data)), 0, -1):
            if data.endswith(needle[:k]):
                return k
        return 0


def _decode(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")


def _clean_output(data: bytes) -> str:
    """Strip terminal control residues so agent-visible output is plain text."""
    data = (
        data.replace(b"\x1b[?2004h", b"")
        .replace(b"\x1b[?2004l", b"")
        .replace(b"\r\n", b"\n")
        .replace(b"\r", b"\n")
    )
    return data.decode("utf-8", errors="replace").strip()


def _disable_echo(attrs: list[Any]) -> list[Any]:
    """Return termios attributes with input echo turned off."""
    attrs[3] = attrs[3] & ~termios.ECHO
    return attrs


def _set_winsize(fd: int, cols: int, rows: int) -> None:
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))