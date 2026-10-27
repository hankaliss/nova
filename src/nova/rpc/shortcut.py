"""Global show/hide shortcut, set in Nova's settings (AD-035)."""

import asyncio
import json
from typing import Any

from nova.kde.shortcut import GlobalShortcut, ShortcutTaken


class ShortcutMixin:
    """Part of :class:`nova.rpc.core.NovaRpc`; state lives on the instance."""

    _shortcut: GlobalShortcut | None = None
    _shortcut_available = False
    _shortcut_recording = False
    _shortcut_status: dict[str, Any] = {"available": False, "shortcut": "", "error": ""}

    async def _start_shortcut(self) -> None:
        """Register with kglobalaccel (KDE only) and apply the setting."""
        self._shortcut = GlobalShortcut(self._on_shortcut_pressed)
        self._shortcut_available = await self._shortcut.start()
        await self._apply_shortcut()

    def _on_shortcut_pressed(self) -> None:
        # While the Settings field records a new key, Nova's current key must
        # not hide the window it is typed into.
        if not self._shortcut_recording:
            self.emit("toggle_window")

    async def _apply_shortcut(self) -> None:
        text = self._settings.get("toggle_shortcut", "")
        status = {"available": self._shortcut_available, "shortcut": "", "error": ""}
        if self._shortcut_available and self._shortcut is not None:
            try:
                status["shortcut"] = await self._shortcut.set_keys(text)
            except (ShortcutTaken, ValueError) as e:
                status["error"] = str(e)
                # Don't keep an older key bound behind the user's back.
                await self._shortcut.set_keys("")
            except Exception as e:
                status["error"] = f"KDE refused the shortcut: {e}"
        elif text:
            status["error"] = "Global shortcuts need KDE Plasma (kglobalaccel)"
        self._shortcut_status = status
        self.emit("shortcut_status", status)

    def shortcutStatus(self) -> str:
        self.emit("shortcut_status", self._shortcut_status)
        return json.dumps(self._shortcut_status)

    def shortcutRecording(self, active: bool) -> None:
        self._shortcut_recording = bool(active)

    def _stop_shortcut(self) -> None:
        if self._shortcut is not None:
            asyncio.ensure_future(self._shortcut.stop())
