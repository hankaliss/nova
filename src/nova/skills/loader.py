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

Two locations: bundled skills ship in the repo (``skills_dir``, read-only
when a ``user_dir`` is set) and skills created from chat live in
``user_dir`` (``~/.local/share/nova/skills`` in the app). A skill's origin
follows from where it lives. Without a ``user_dir`` the loader manages
``skills_dir`` directly (single-directory mode).
"""

import importlib.util
import json
import re
import shutil
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nova.tools.registry import ToolRegistry
from nova.tools.tool import Tool

_SKILL_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass
class Skill:
    name: str
    path: Path
    description: str
    tool_names: list[str] = field(default_factory=list)
    enabled: bool = True
    origin: str = "bundled"


def _parse_skill_md_lines(lines: list[str]) -> str:
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return ""


def _parse_skill_md(skill_md: Path) -> str:
    try:
        lines = skill_md.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    return _parse_skill_md_lines(lines)


class SkillLoader:
    """Discovers skills and manages their tools in a registry."""

    def __init__(
        self,
        skills_dir: str | Path,
        registry: ToolRegistry | None = None,
        store=None,
        user_dir: str | Path | None = None,
    ) -> None:
        self.skills_dir = Path(skills_dir)
        self.user_dir = Path(user_dir) if user_dir is not None else None
        self.registry = registry
        self.store = store
        self._modules: dict[str, Any] = {}

    @property
    def write_dir(self) -> Path:
        """Where create/update/delete operate."""
        return self.user_dir if self.user_dir is not None else self.skills_dir

    # ---- Discovery ------------------------------------------------------

    def _skill_dirs(self) -> list[tuple[Path, str]]:
        """(directory, origin) pairs, bundled first; first name wins."""
        if self.user_dir is None or self.user_dir == self.skills_dir:
            return [(self.skills_dir, "bundled")]
        return [(self.skills_dir, "bundled"), (self.user_dir, "user")]

    def _find_dir(self, name: str) -> tuple[Path, str] | None:
        for base, origin in self._skill_dirs():
            if (base / name / "SKILL.md").is_file():
                return base / name, origin
        return None

    def discover(self) -> list[Skill]:
        """Find all skill directories containing a SKILL.md."""
        skills = []
        seen: set[str] = set()
        for base, origin in self._skill_dirs():
            if not base.is_dir():
                continue
            for entry in sorted(base.iterdir()):
                skill_md = entry / "SKILL.md"
                if not (entry.is_dir() and skill_md.is_file()):
                    continue
                if entry.name in seen:
                    continue  # a user skill can't shadow a bundled one
                seen.add(entry.name)
                skills.append(
                    Skill(
                        name=entry.name,
                        path=entry,
                        description=_parse_skill_md(skill_md),
                        tool_names=[t.name for t in self._tools(entry)],
                        enabled=self._is_enabled(entry.name),
                        origin=origin,
                    )
                )
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

    # ---- Skill management (skill maker) -------------------------------

    def _validate_name(self, name: str) -> None:
        if not _SKILL_NAME_RE.match(name):
            raise ValueError(
                "Invalid skill name {!r}: use lowercase letters, digits and "
                "underscores, starting with a letter".format(name)
            )

    def _validate_skill_md(self, skill_md: str) -> str:
        text = skill_md.strip()
        if not text:
            raise ValueError("skill_md must not be empty")
        if not _parse_skill_md_lines(text.splitlines()):
            raise ValueError(
                "SKILL.md must start with a '# ' heading describing the skill"
            )
        return text

    def _writable_dir(self, name: str) -> Path:
        """Existing skill dir that management may modify, or raise."""
        found = self._find_dir(name)
        if found is None:
            raise ValueError(f"Skill '{name}' not found")
        path, origin = found
        if self.user_dir is not None and origin == "bundled":
            raise ValueError(
                f"Skill '{name}' is bundled with Nova and read-only; create a "
                "new skill with a different name instead"
            )
        return path

    def _stage_skill(self, name: str, skill_md: str, tools_py: str) -> list[Tool]:
        """Fully validate skill content without writing. Returns the Tool list."""
        self._validate_name(name)
        if self._find_dir(name) is not None:
            raise ValueError(f"Skill '{name}' already exists")
        self._validate_skill_md(skill_md)
        tools = self._validate_tools_py(name, tools_py)
        if self.registry is not None:
            for tool in tools:
                if tool.name in self.registry._tools:
                    raise ValueError(
                        f"Tool '{tool.name}' is already registered"
                    )
        return tools

    def _validate_tools_py(self, name: str, tools_py: str) -> list[Tool]:
        text = tools_py.strip()
        if not text:
            raise ValueError("tools_py must not be empty")
        try:
            compile(text, f"<skill {name}/tools.py>", "exec")
        except SyntaxError as e:
            raise ValueError(f"tools.py has a syntax error: {e.msg} (line {e.lineno})")
        module = types.ModuleType(f"_skill_preview_{name}")
        try:
            exec(text, module.__dict__)
        except Exception as e:
            raise ValueError(
                f"tools.py failed to import: {type(e).__name__}: {e}"
            )
        get_tools = getattr(module, "get_tools", None)
        if not callable(get_tools):
            raise ValueError("tools.py must define get_tools() returning a list of Tool")
        try:
            tools = list(get_tools())
        except Exception as e:
            raise ValueError(f"get_tools() failed: {type(e).__name__}: {e}")
        prefix = f"{name}."
        for tool in tools:
            if not isinstance(tool, Tool):
                raise ValueError(f"get_tools() returned a non-Tool: {tool!r}")
            if not tool.name.startswith(prefix):
                raise ValueError(
                    f"Tool name '{tool.name}' must start with '{prefix}'"
                )
        return tools

    def create_skill(self, name: str, skill_md: str, tools_py: str) -> tuple[int, str]:
        """Create a new skill, write its files and register its tools.

        Raises ValueError on any invalid input (nothing is written).
        Returns (tool_count, description).
        """
        tools = self._stage_skill(name, skill_md, tools_py)
        skill_dir = self.write_dir / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(
            self._validate_skill_md(skill_md) + "\n", encoding="utf-8"
        )
        (skill_dir / "tools.py").write_text(tools_py.strip() + "\n", encoding="utf-8")
        self._modules.pop(str(skill_dir), None)
        if self.registry is not None:
            for tool in tools:
                self.registry.register(tool)
        return len(tools), _parse_skill_md(skill_dir / "SKILL.md")

    def update_skill(
        self,
        name: str,
        skill_md: str | None = None,
        tools_py: str | None = None,
    ) -> int:
        """Overwrite the provided files of an existing skill and re-register.

        Returns the number of tools registered. Raises ValueError for bad
        content or a missing skill (files are only written when valid).
        """
        skill_dir = self._writable_dir(name)
        new_tools: list[Tool] | None = None
        if tools_py is not None:
            if not tools_py.strip():
                raise ValueError("tools_py must not be empty")
            new_tools = self._validate_tools_py(name, tools_py)
        if skill_md is not None:
            self._validate_skill_md(skill_md)
        if self.registry is not None and new_tools is not None:
            skill = next((s for s in self.discover() if s.name == name), None)
            if skill is None:
                raise ValueError(f"Skill '{name}' not found")
            mine = set(skill.tool_names)
            for tool in new_tools:
                if tool.name in self.registry._tools and tool.name not in mine:
                    raise ValueError(f"Tool '{tool.name}' is already registered")
            self.unregister(skill)
        if skill_md is not None:
            (skill_dir / "SKILL.md").write_text(
                self._validate_skill_md(skill_md) + "\n", encoding="utf-8"
            )
        if tools_py is not None:
            (skill_dir / "tools.py").write_text(tools_py.strip() + "\n", encoding="utf-8")
        self._modules.pop(str(skill_dir), None)
        return self._register_skill_from_disk(name)

    def _register_skill_from_disk(self, name: str) -> int:
        if self.registry is None:
            return 0
        skill = next((s for s in self.discover() if s.name == name), None)
        if skill is None:
            return 0
        return self.register(skill)

    def delete_skill(self, name: str) -> int:
        """Remove a skill directory and unregister its tools. Returns count removed."""
        skill_dir = self._writable_dir(name)
        skill = next((s for s in self.discover() if s.name == name), None)
        count = 0
        if skill is not None:
            count = self.unregister(skill)
        shutil.rmtree(skill_dir)
        self._modules.pop(str(skill_dir), None)
        return count

    def get_skill_content(self, name: str) -> dict[str, str] | None:
        """Return {skill_md, tools_py} for a skill, or None if missing."""
        found = self._find_dir(name)
        if found is None:
            return None
        skill_dir = found[0]
        skill_md = skill_dir / "SKILL.md"
        content: dict[str, str] = {"skill_md": skill_md.read_text(encoding="utf-8")}
        tools_py = skill_dir / "tools.py"
        if tools_py.is_file():
            content["tools_py"] = tools_py.read_text(encoding="utf-8")
        return content

    # ---- UI -------------------------------------------------------------

    def to_json(self) -> str:
        """Serialize skills for the UI (name, description, enabled, tools)."""
        return json.dumps(
            [
                {
                    "name": skill.name,
                    "description": skill.description,
                    "enabled": skill.enabled,
                    "origin": skill.origin,
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