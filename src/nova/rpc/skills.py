"""Skills: list, toggle, read, save from the chat's fallback card."""

import json
import re


class SkillsMixin:
    """Part of :class:`nova.rpc.core.NovaRpc`; state lives on the instance."""

    def skills(self) -> str:
        if self._skill_loader is None:
            self.emit("skill_list", [])
            return "[]"
        data = json.loads(self._skill_loader.to_json())
        self.emit("skill_list", data)
        return json.dumps(data)

    def toggleSkill(self, name: str, enabled: bool) -> None:
        if self._skill_loader is None:
            return
        if self._skill_loader.set_enabled(name, enabled):
            state = "enabled" if enabled else "disabled"
            self.emit("statusChanged", f"Skill '{name}' {state}")
            self.emit("skill_list", json.loads(self._skill_loader.to_json()))
        else:
            self.emit("statusChanged", f"Skill '{name}' not found")

    def getSkill(self, name: str) -> str:
        """Return a skill's files (SKILL.md, tools.py) as JSON."""
        if self._skill_loader is None:
            return "{}"
        content = self._skill_loader.get_skill_content(name)
        self.emit("skill_content", content if content is not None else {})
        return json.dumps(content or {})

    async def createSkill(self, name: str, skill_md: str, tools_py: str) -> str:
        """Save a skill from the frontend's "Save skill" fallback card.

        This is the UI counterpart of the LLM's nova.skill.create tool — it
        calls the same engine (_create) so validation, disk writes, smoke
        tests and the user skills directory are identical. Saving loads Python
        into this process, so it goes through the same DESTRUCTIVE permission
        gate (the dialog shows the code) — a click on the card is not enough.
        """
        if self._skill_loader is None:
            raise RuntimeError("Skills unavailable")
        if not name.strip():
            title = re.sub(r"^#+\s*", "", skill_md.splitlines()[0]).strip()
            name = re.sub(r"\s+", "_", title.lower())
            name = re.sub(r"[^a-z0-9_]", "", name).strip("_")
        if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
            raise ValueError(
                f"Invalid skill name {name!r} — use lowercase letters, "
                "digits and underscores."
            )
        # The model often writes a tool prefix that disagrees with the chosen
        # name (heading/path typo). Align every Tool(...) prefix to the name
        # so validation always passes; the frontend does the same, this is the
        # safety net for any caller.
        tools_py = re.sub(
            r"name\s*=\s*(['\"])[a-z0-9_]+\.(?=[a-z0-9_]+['\"])",
            lambda m: f"name={m.group(1)}{name}.",
            tools_py,
        )
        tools_py = re.sub(
            r"Tool\(\s*(['\"])[a-z0-9_]+\.(?=[a-z0-9_]+['\"])",
            lambda m: f"Tool({m.group(1)}{name}.",
            tools_py,
        )
        from nova.tools import skill_maker

        # Re-saving an existing skill (e.g. retrying a save whose smoke test
        # failed but files were written) must update it, not error out. The
        # user explicitly clicked save, so overwrite is the requested intent.
        exists = self._skill_loader.get_skill_content(name) is not None
        tool = self._registry.get(
            "nova.skill.update" if exists else "nova.skill.create"
        )
        await self._registry.permissions.authorize(
            tool, {"name": name, "skill_md": skill_md, "tools_py": tools_py}
        )
        if exists:
            summary = await skill_maker._update(
                self._skill_loader, name, skill_md, tools_py
            )
        else:
            summary = await skill_maker._create(
                self._skill_loader, self._store, name, skill_md, tools_py
            )
        self.emit("skill_list", json.loads(self._skill_loader.to_json()))
        return summary
