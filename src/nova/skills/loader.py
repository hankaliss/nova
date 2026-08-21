"""Skill loader for Nova.

Skills follow the Markdown + Python format:

```text
skills/<name>/
├── SKILL.md    # name and description of the skill
└── tools.py    # get_tools() returning a list of Tool
```

A skill's tools are registered into the ToolRegistry when the skill is
enabled (default). Enabled state is persisted in the settings table via
``MemoryStore`` under ``skill_enabled_<name>``.
"""

import importlib.util
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nova.tools.registry import ToolRegistry
from nova.tools.tool import Tool


@dataclass
class Skill:
    name: str
    path: Path
    description: str
    tool_names: list[str] = field(default_factory=list)
    enabled: bool = True


def _parse_skill_md(skill_md: Path) -> str:
    try:
        lines = skill_md.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return ""


class SkillLoader:
    """Discovers skills and manages their tools in a registry."""

    def __init__(
        self,
        skills_dir: str | Path,
        registry: ToolRegistry | None = None,
        store=None,
    ) -> None:
        self.skills_dir = Path(skills_dir)
        self.registry = registry
        self.store = store
        self._modules: dict[str, Any] = {}

    # ---- Discovery ------------------------------------------------------

    def discover(self) -> list[Skill]:
        """Find all skill directories containing a SKILL.md."""
        if not self.skills_dir.is_dir():
            return []
        skills = []
        for entry in sorted(self.skills_dir.iterdir()):
            skill_md = entry / "SKILL.md"
            if entry.is_dir() and skill_md.is_file():
                skill = Skill(
                    name=entry.name,
                    path=entry,
                    description=_parse_skill_md(skill_md),
                    tool_names=[t.name for t in self._tools(entry)],
                    enabled=self._is_enabled(entry.name),
                )
                skills.append(skill)
        return skills

    def _is_enabled(self, name: str) -> bool:
        if self.store is None:
            return True
        stored = self.store.get_setting(f"skill_enabled_{name}")
        if stored is None:
            return True
        return stored == "true"

    # ---- Module loading -------------------------------------------------

    def _load_module(self, skill_path: Path) -> Any | None:
        key = str(skill_path)
        if key in self._modules:
            return self._modules[key]
        tools_py = skill_path / "tools.py"
        if not tools_py.is_file():
            return None
        module_name = f"nova_skill_{skill_path.name}"
        spec = importlib.util.spec_from_file_location(module_name, tools_py)
        if spec is None or spec.loader is None:
            raise ValueError(f"Cannot load tools.py for skill '{skill_path.name}'")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self._modules[key] = module
        return module

    def _tools(self, skill_path: Path) -> list[Tool]:
        module = self._load_module(skill_path)
        if module is None:
            return []
        get_tools = getattr(module, "get_tools", None)
        if not callable(get_tools):
            raise ValueError(
                f"Skill '{skill_path.name}' tools.py must define get_tools()"
            )
        return list(get_tools())

    # ---- Registration ---------------------------------------------------

    def register(self, skill: Skill, registry: ToolRegistry | None = None) -> int:
        """Register the skill's tools. Returns the number of tools registered."""
        registry = registry or self.registry
        if registry is None:
            raise ValueError("No ToolRegistry available")
        count = 0
        for tool in self._tools(skill.path):
            registry.register(tool)
            count += 1
        return count

    def unregister(self, skill: Skill, registry: ToolRegistry | None = None) -> int:
        """Unregister the skill's tools. Returns the number removed."""
        registry = registry or self.registry
        if registry is None:
            return 0
        count = 0
        for name in skill.tool_names:
            if registry.unregister(name):
                count += 1
        return count

    def set_enabled(self, name: str, enabled: bool) -> bool:
        """Enable or disable a skill, persist the choice and update the registry."""
        skill = next((s for s in self.discover() if s.name == name), None)
        if skill is None:
            return False
        if self.store is not None:
            self.store.set_setting(
                f"skill_enabled_{name}", "true" if enabled else "false"
            )
        if self.registry is not None:
            if enabled:
                self.register(skill)
            else:
                self.unregister(skill)
        return True

    def register_all(self, registry: ToolRegistry | None = None) -> int:
        """Register all enabled skills. Returns the total number of tools."""
        total = 0
        for skill in self.discover():
            if skill.enabled:
                total += self.register(skill, registry)
        return total

    # ---- UI -------------------------------------------------------------

    def to_json(self) -> str:
        """Serialize skills for the UI (name, description, enabled, tools)."""
        return json.dumps(
            [
                {
                    "name": skill.name,
                    "description": skill.description,
                    "enabled": skill.enabled,
                    "tools": [
                        {
                            "name": tool.name,
                            "risk": tool.risk.value,
                        }
                        for tool in self._tools(skill.path)
                    ],
                }
                for skill in self.discover()
            ]
        )