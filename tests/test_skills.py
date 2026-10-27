"""Tests for SkillLoader (Markdown + Python skill format)."""

from pathlib import Path

import pytest

from nova.skills.loader import SkillLoader
from nova.tools.registry import ToolRegistry


def make_skill_dir(
    tmp_path: Path,
    name: str = "hello",
    with_tools: bool = True,
    tool_name: str | None = None,
) -> Path:
    skill = tmp_path / name
    skill.mkdir()
    (skill / "SKILL.md").write_text(f"# {name.title()} Skill\n\nDoes something useful.\n")
    tool_name = tool_name or f"{name}.info"
    if with_tools:
        (skill / "tools.py").write_text(
            "from nova.tools.tool import RiskLevel, Tool\n\n"
            f"def _info() -> dict:\n"
            f"    return {{'skill': '{name}'}}\n\n"
            "def get_tools() -> list[Tool]:\n"
            "    return [Tool(\n"
            f"        name='{tool_name}',\n"
            "        description='Provides skill info.',\n"
            "        parameters={'type': 'object', 'properties': {}},\n"
            "        risk=RiskLevel.READ,\n"
            f"        permission='{tool_name}',\n"
            "        implementation=_info,\n"
            "    )]\n"
        )
    return skill


def test_discover_finds_skills(tmp_path):
    make_skill_dir(tmp_path, "hello")
    make_skill_dir(tmp_path, "world")

    loader = SkillLoader(tmp_path)
    skills = loader.discover()

    assert len(skills) == 2
    names = {s.name for s in skills}
    assert names == {"hello", "world"}


def test_discover_skips_dirs_without_skill_md(tmp_path):
    make_skill_dir(tmp_path, "hello")
    (tmp_path / "notaskill").mkdir()

    loader = SkillLoader(tmp_path)
    skills = loader.discover()
    assert len(skills) == 1
    assert skills[0].name == "hello"


def test_discover_missing_dir_returns_empty(tmp_path):
    loader = SkillLoader(tmp_path / "nope")
    assert loader.discover() == []


def test_skill_description_from_heading(tmp_path):
    make_skill_dir(tmp_path, "hello")
    loader = SkillLoader(tmp_path)
    skills = loader.discover()
    assert skills[0].description == "Hello Skill"


def test_register_loads_tools(tmp_path):
    make_skill_dir(tmp_path, "hello")
    loader = SkillLoader(tmp_path)
    registry = ToolRegistry()

    skill = loader.discover()[0]
    count = loader.register(skill, registry)

    assert count == 1
    tool = registry.get("hello.info")
    assert tool.description == "Provides skill info."
    assert tool.risk.value == "read"


def test_register_skill_without_tools_py(tmp_path):
    make_skill_dir(tmp_path, "bare", with_tools=False)
    loader = SkillLoader(tmp_path)
    registry = ToolRegistry()

    skill = loader.discover()[0]
    count = loader.register(skill, registry)
    assert count == 0


def test_register_all(tmp_path):
    make_skill_dir(tmp_path, "hello")
    make_skill_dir(tmp_path, "world", tool_name="world.info")
    loader = SkillLoader(tmp_path)
    registry = ToolRegistry()

    total = loader.register_all(registry)
    assert total == 2
    assert registry.get("hello.info")
    assert registry.get("world.info")


def test_register_missing_get_tools_raises(tmp_path):
    skill = tmp_path / "bad"
    skill.mkdir()
    (skill / "SKILL.md").write_text("# Bad Skill\n")
    (skill / "tools.py").write_text("x = 1\n")

    loader = SkillLoader(tmp_path)
    registry = ToolRegistry()
    with pytest.raises(ValueError, match="get_tools"):
        loader.register(loader.discover()[0], registry)


@pytest.mark.asyncio
async def test_skill_tool_executes(tmp_path):
    make_skill_dir(tmp_path, "hello")
    loader = SkillLoader(tmp_path)
    registry = ToolRegistry()
    loader.register_all(registry)

    result = await registry.execute("hello.info")
    assert result.success is True
    assert result.data == {"skill": "hello"}

def test_discover_includes_tool_names(tmp_path):
    make_skill_dir(tmp_path, "hello")
    loader = SkillLoader(tmp_path)

    skill = loader.discover()[0]

    assert skill.tool_names == ["hello.info"]


def test_discover_defaults_enabled(tmp_path):
    make_skill_dir(tmp_path, "hello")
    loader = SkillLoader(tmp_path)

    assert loader.discover()[0].enabled is True


def test_set_enabled_persists_and_updates_registry(tmp_path):
    from nova.memory.store import MemoryStore

    make_skill_dir(tmp_path, "hello")

    store = MemoryStore(tmp_path / "nova.db")
    registry = ToolRegistry()
    loader = SkillLoader(tmp_path, registry=registry, store=store)
    loader.register_all(registry)
    assert registry.get("hello.info")

    assert loader.set_enabled("hello", False) is True
    assert registry._tools == {}
    assert store.get_setting("skill_enabled_hello") == "false"
    assert loader.discover()[0].enabled is False

    assert loader.set_enabled("hello", True) is True
    assert registry.get("hello.info")
    assert store.get_setting("skill_enabled_hello") == "true"
    assert loader.discover()[0].enabled is True


def test_register_all_skips_disabled(tmp_path):
    from nova.memory.store import MemoryStore

    make_skill_dir(tmp_path, "hello")

    store = MemoryStore(tmp_path / "nova.db")
    registry = ToolRegistry()
    store.set_setting("skill_enabled_hello", "false")
    loader = SkillLoader(tmp_path, registry=registry, store=store)

    total = loader.register_all(registry)

    assert total == 0
    assert registry._tools == {}
    assert loader.discover()[0].enabled is False


def test_set_enabled_unknown_skill_returns_false(tmp_path):
    loader = SkillLoader(tmp_path)
    assert loader.set_enabled("nope", True) is False


def test_to_json_shape(tmp_path):
    make_skill_dir(tmp_path, "hello")
    loader = SkillLoader(tmp_path)

    import json

    data = json.loads(loader.to_json())

    assert len(data) == 1
    assert data[0]["name"] == "hello"
    assert data[0]["enabled"] is True
    assert data[0]["tools"] == [{"name": "hello.info", "risk": "read"}]
