"""Settings, system prompt (agent.md) and persisted permission grants."""

import asyncio
import json
from pathlib import Path

from nova.rpc.paths import RESOURCES_DIR


class SettingsMixin:
    """Part of :class:`nova.rpc.core.NovaRpc`; state lives on the instance."""

    def _restore_settings(self) -> None:
        if self._store is None:
            return
        # Keys from before user skills moved out of the repo; no longer read.
        for key, _ in self._store.list_settings():
            if key.startswith("skill_origin_"):
                self._store.delete_setting(key)
        for key in self._settings:
            stored = self._store.get_setting(key)
            if stored is not None:
                self._settings[key] = stored

    def _prompt_files(self) -> list[tuple[str, Path]]:
        """Ordered candidate prompt files: (label, path)."""
        return [
            ("user file", Path.home() / ".config" / "nova" / "agent.md"),
            ("bundled file", RESOURCES_DIR / "agent.md"),
        ]

    def _resolve_system_prompt(self) -> tuple[str, str]:
        """Return (content, source_label) of the currently active system prompt."""
        for label, path in self._prompt_files():
            if path.is_file():
                content = path.read_text().strip()
                if content:
                    return content, f"{label} ({path})"
        stored = self._settings.get("system_prompt", "")
        if stored:
            return stored, "saved in app settings"
        return "You are Nova, a helpful local AI desktop assistant.", "built-in default"

    def _load_system_prompt(self) -> str:
        """Load the active system prompt (agent.md → settings → default)."""
        return self._resolve_system_prompt()[0]

    def _persist_user_prompt(self, text: str) -> str:
        """Write the edited prompt to ~/.config/nova/agent.md (deleted when empty).

        Returns the prompt text that should become active now.
        """
        user_path = Path.home() / ".config" / "nova" / "agent.md"
        if text.strip():
            user_path.parent.mkdir(parents=True, exist_ok=True)
            user_path.write_text(text.strip() + "\n")
        else:
            user_path.unlink(missing_ok=True)
        return self._resolve_system_prompt()[0]

    def _restore_grants(self) -> None:
        if self._store is None:
            return
        for key, value in self._store.list_settings():
            if key.startswith("perm_grant_") and value == "true":
                self._registry.permissions.grant(key.removeprefix("perm_grant_"))

    def getSettings(self) -> str:
        payload = dict(self._settings)
        content, source = self._resolve_system_prompt()
        payload["system_prompt"] = content
        payload["system_prompt_source"] = source
        self.emit("settings", payload)
        return json.dumps(payload)

    def saveSettings(self, settings_json: str) -> None:
        try:
            incoming = json.loads(settings_json)
        except json.JSONDecodeError:
            self.emit("statusChanged", "Error: invalid settings data")
            return
        previous_ctx = self._settings.get("num_ctx")
        previous_shortcut = self._settings.get("toggle_shortcut")
        for key, value in incoming.items():
            if key not in self._settings:
                continue
            text = str(value)
            self._settings[key] = text
            if self._store is not None:
                self._store.set_setting(key, text)
        if "system_prompt" in incoming:
            effective = self._persist_user_prompt(self._settings["system_prompt"])
            if self._agent is not None:
                self._agent.context.system_prompt = effective
        # Always re-emit: the Electron main process applies window settings
        # (always_on_top) from this event.
        payload = dict(self._settings)
        payload["system_prompt"], payload["system_prompt_source"] = (
            self._resolve_system_prompt()
        )
        self.emit("settings", payload)
        if self._agent is not None:
            self._agent.options = {
                "temperature": float(self._settings["temperature"]),
                "num_ctx": self._resolved_num_ctx(),
            }
            # Refitting reloads the model, so only when the context setting
            # itself changed.
            if self._settings.get("num_ctx") != previous_ctx:
                self._apply_context()
        if self._registry.permissions is not None:
            self._registry.permissions.auto_approve_writes = (
                self._settings["auto_approve_writes"] == "true"
            )
        if self._settings.get("toggle_shortcut") != previous_shortcut:
            asyncio.ensure_future(self._apply_shortcut())
        self.emit("statusChanged", "Settings saved")

    def allowClose(self) -> bool:
        return self._settings["close_to_tray"] != "true"
