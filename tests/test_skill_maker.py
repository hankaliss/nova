"""Tests for the skill maker: nova.skill.* tools + SkillLoader management."""

import json

import pytest

from nova.memory.store import MemoryStore
from nova.security.permissions import PermissionSystem
from nova.skills.loader import SkillLoader
from nova.tools.registry import ToolRegistry

SKILL_MD = "# Gets a one-line weather summary for a city.\n\nUseful for travel plans."
TOOLS_PY = '''\
from nova.tools.tool import RiskLevel, Tool


def _weather(city: str) -> str:
    return f"weather in {city}: sunny"


def get_tools() -> list[Tool]:
    return [
        Tool(
            name="weather.get",
            description="Get a one-line weather summary for a city.",
            parameters={
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
            risk=RiskLevel.READ,
            permission="weather.get",
            implementation=_weather,
        )
    ]
'''


def make_loader(tmp_path, with_store=True):
    store = MemoryStore(tmp_path / "nova.db") if with_store else None
    registry = ToolRegistry(
        permissions=PermissionSystem(approval_callback=lambda tool, args: True)
    )
    # Skills are created in tmp_path (the user dir); bundled dir is separate.
    loader = SkillLoader(
        tmp_path / "bundled", registry=registry, store=store, user_dir=tmp_path
    )
    return loader, registry, store


def test_create_skill_writes_files_and_registers(tmp_path):
    loader, registry, _ = make_loader(tmp_path)

    count, desc = loader.create_skill("weather", SKILL_MD, TOOLS_PY)

    assert count == 1
    assert desc == "Gets a one-line weather summary for a city."
    assert (tmp_path / "weather" / "SKILL.md").is_file()
    assert (tmp_path / "weather" / "tools.py").is_file()
    assert registry.get("weather.get")
    skill = loader.discover()[0]
    assert skill.name == "weather"
    assert skill.origin == "user"


@pytest.mark.asyncio
async def test_create_skill_via_tool_marks_user_origin(tmp_path):
    loader, registry, store = make_loader(tmp_path)
    tools = registry.register_skill_maker_tools(loader, store)
    names = {t.name for t in tools}
    assert names == {
        "nova.skill.create",
        "nova.skill.update",
        "nova.skill.delete",
        "nova.skill.get",
    }

    result = await registry.execute(
        "nova.skill.create",
        {"name": "weather", "skill_md": SKILL_MD, "tools_py": TOOLS_PY},
    )
    assert result.success is True
    assert loader.discover()[0].origin == "user"
    assert json.loads(loader.to_json())[0]["origin"] == "user"


@pytest.mark.asyncio
async def test_created_skill_tool_runs(tmp_path):
    loader, registry, store = make_loader(tmp_path)
    registry.register_skill_maker_tools(loader, store)
    await registry.execute(
        "nova.skill.create",
        {"name": "weather", "skill_md": SKILL_MD, "tools_py": TOOLS_PY},
    )

    result = await registry.execute("weather.get", {"city": "Berlin"})
    assert result.success is True
    assert result.data == "weather in Berlin: sunny"


@pytest.mark.asyncio
async def test_created_skill_can_be_disabled_like_any_other(tmp_path):
    loader, registry, store = make_loader(tmp_path)
    registry.register_skill_maker_tools(loader, store)
    await registry.execute(
        "nova.skill.create",
        {"name": "weather", "skill_md": SKILL_MD, "tools_py": TOOLS_PY},
    )
    assert "weather.get" in registry._tools

    assert loader.set_enabled("weather", False) is True
    assert "weather.get" not in registry._tools
    assert store.get_setting("skill_enabled_weather") == "false"


@pytest.mark.asyncio
async def test_create_rejects_invalid_name(tmp_path):
    loader, registry, store = make_loader(tmp_path)
    registry.register_skill_maker_tools(loader, store)

    result = await registry.execute(
        "nova.skill.create",
        {"name": "Bad Skill!", "skill_md": SKILL_MD, "tools_py": TOOLS_PY},
    )
    assert result.success is False
    assert "skill name" in result.error
    assert not (tmp_path / "Bad Skill!").exists()


@pytest.mark.asyncio
async def test_create_rejects_syntax_error_without_writing(tmp_path):
    loader, registry, store = make_loader(tmp_path)
    registry.register_skill_maker_tools(loader, store)

    result = await registry.execute(
        "nova.skill.create",
        {"name": "broken", "skill_md": SKILL_MD, "tools_py": "def get_tools(:\n"},
    )
    assert result.success is False
    assert "syntax error" in result.error
    assert not (tmp_path / "broken").exists()


@pytest.mark.asyncio
async def test_create_rejects_missing_get_tools(tmp_path):
    loader, registry, store = make_loader(tmp_path)
    registry.register_skill_maker_tools(loader, store)

    result = await registry.execute(
        "nova.skill.create",
        {"name": "broken", "skill_md": SKILL_MD, "tools_py": "x = 1\n"},
    )
    assert result.success is False
    assert "get_tools" in result.error
    assert not (tmp_path / "broken").exists()


@pytest.mark.asyncio
async def test_create_rejects_wrong_tool_prefix(tmp_path):
    loader, registry, store = make_loader(tmp_path)
    registry.register_skill_maker_tools(loader, store)

    bad = TOOLS_PY.replace("weather.get", "other.get")
    result = await registry.execute(
        "nova.skill.create",
        {"name": "weather", "skill_md": SKILL_MD, "tools_py": bad},
    )
    assert result.success is False
    assert "must start with" in result.error
    assert not (tmp_path / "weather").exists()


@pytest.mark.asyncio
async def test_create_requires_skill_md_heading(tmp_path):
    loader, registry, store = make_loader(tmp_path)
    registry.register_skill_maker_tools(loader, store)

    result = await registry.execute(
        "nova.skill.create",
        {"name": "weather", "skill_md": "no heading here", "tools_py": TOOLS_PY},
    )
    assert result.success is False
    assert "heading" in result.error


@pytest.mark.asyncio
async def test_update_modifies_tools_and_reregisters(tmp_path):
    loader, registry, store = make_loader(tmp_path)
    registry.register_skill_maker_tools(loader, store)
    await registry.execute(
        "nova.skill.create",
        {"name": "weather", "skill_md": SKILL_MD, "tools_py": TOOLS_PY},
    )

    new_py = TOOLS_PY.replace("sunny", "cloudy")
    result = await registry.execute(
        "nova.skill.update",
        {"name": "weather", "skill_md": "", "tools_py": new_py},
    )
    assert result.success is True
    assert "cloudy" in (tmp_path / "weather" / "tools.py").read_text()
    assert "sunny" not in (tmp_path / "weather" / "tools.py").read_text()

    result = await registry.execute("weather.get", {"city": "Rome"})
    assert result.success is True
    assert result.data == "weather in Rome: cloudy"


@pytest.mark.asyncio
async def test_update_unknown_skill_fails(tmp_path):
    loader, registry, store = make_loader(tmp_path)
    registry.register_skill_maker_tools(loader, store)

    result = await registry.execute(
        "nova.skill.update",
        {"name": "nope", "skill_md": "", "tools_py": TOOLS_PY},
    )
    assert result.success is False
    assert "not found" in result.error


@pytest.mark.asyncio
async def test_get_skill_content(tmp_path):
    loader, registry, store = make_loader(tmp_path)
    registry.register_skill_maker_tools(loader, store)
    await registry.execute(
        "nova.skill.create",
        {"name": "weather", "skill_md": SKILL_MD, "tools_py": TOOLS_PY},
    )

    content = loader.get_skill_content("weather")
    assert content is not None
    assert content["skill_md"].startswith("# ")
    assert "def get_tools" in content["tools_py"]
    assert loader.get_skill_content("missing") is None


@pytest.mark.asyncio
async def test_get_skill_read_tool(tmp_path):
    loader, registry, store = make_loader(tmp_path)
    registry.register_skill_maker_tools(loader, store)
    await registry.execute(
        "nova.skill.create",
        {"name": "weather", "skill_md": SKILL_MD, "tools_py": TOOLS_PY},
    )

    result = await registry.execute("nova.skill.get", {"name": "weather"})
    assert result.success is True
    data = json.loads(result.data)
    assert "def get_tools" in data["tools_py"]


@pytest.mark.asyncio
async def test_delete_removes_skill_and_unregisters(tmp_path):
    loader, registry, store = make_loader(tmp_path)
    registry.register_skill_maker_tools(loader, store)
    await registry.execute(
        "nova.skill.create",
        {"name": "weather", "skill_md": SKILL_MD, "tools_py": TOOLS_PY},
    )
    result = await registry.execute("nova.skill.delete", {"name": "weather"})
    assert result.success is True
    assert not (tmp_path / "weather").exists()
    assert "weather.get" not in registry._tools
    assert store.get_setting("skill_enabled_weather") is None
    assert loader.discover() == []


@pytest.mark.asyncio
async def test_delete_unknown_skill_fails(tmp_path):
    loader, registry, store = make_loader(tmp_path)
    registry.register_skill_maker_tools(loader, store)

    result = await registry.execute("nova.skill.delete", {"name": "nope"})
    assert result.success is False
    assert "not found" in result.error


@pytest.mark.asyncio
async def test_to_json_includes_origin(tmp_path):
    loader, registry, store = make_loader(tmp_path)
    registry.register_skill_maker_tools(loader, store)
    await registry.execute(
        "nova.skill.create",
        {"name": "weather", "skill_md": SKILL_MD, "tools_py": TOOLS_PY},
    )

    data = json.loads(loader.to_json())
    assert data[0]["origin"] == "user"
    assert data[0]["enabled"] is True
    assert data[0]["tools"] == [{"name": "weather.get", "risk": "read"}]

def test_create_and_update_are_destructive(tmp_path):
    from nova.skills.loader import SkillLoader
    from nova.tools.skill_maker import build
    from nova.tools.tool import RiskLevel

    tools = {t.name: t for t in build(SkillLoader(tmp_path))}
    assert tools["nova.skill.create"].risk == RiskLevel.DESTRUCTIVE
    assert tools["nova.skill.update"].risk == RiskLevel.DESTRUCTIVE
    assert tools["nova.skill.get"].risk == RiskLevel.READ


# ---- Bundled vs user skill locations -------------------------------------


def _two_dir_loader(tmp_path):
    bundled = tmp_path / "bundled"
    user = tmp_path / "user"
    (bundled / "weather").mkdir(parents=True)
    (bundled / "weather" / "SKILL.md").write_text(SKILL_MD)
    (bundled / "weather" / "tools.py").write_text(TOOLS_PY)
    registry = ToolRegistry(
        permissions=PermissionSystem(approval_callback=lambda tool, args: True)
    )
    loader = SkillLoader(bundled, registry=registry, user_dir=user)
    return loader, bundled, user


def test_created_skills_go_to_user_dir(tmp_path):
    loader, bundled, user = _two_dir_loader(tmp_path)
    loader.create_skill("forecast", "# Forecast\n", TOOLS_PY.replace("weather.", "forecast."))
    assert (user / "forecast" / "tools.py").is_file()
    assert not (bundled / "forecast").exists()
    origins = {s.name: s.origin for s in loader.discover()}
    assert origins == {"weather": "bundled", "forecast": "user"}


def test_bundled_skills_are_read_only(tmp_path):
    loader, bundled, _ = _two_dir_loader(tmp_path)
    with pytest.raises(ValueError, match="read-only"):
        loader.update_skill("weather", skill_md="# Changed\n")
    with pytest.raises(ValueError, match="read-only"):
        loader.delete_skill("weather")
    with pytest.raises(ValueError, match="already exists"):
        loader.create_skill("weather", SKILL_MD, TOOLS_PY)
    assert (bundled / "weather" / "SKILL.md").read_text() == SKILL_MD
    # still readable for the LLM
    assert "def get_tools" in loader.get_skill_content("weather")["tools_py"]


def test_user_skill_cannot_shadow_bundled(tmp_path):
    loader, _, user = _two_dir_loader(tmp_path)
    (user / "weather").mkdir(parents=True)
    (user / "weather" / "SKILL.md").write_text("# Impostor\n")
    skills = loader.discover()
    assert len(skills) == 1
    assert skills[0].origin == "bundled"
    assert skills[0].description != "Impostor"
