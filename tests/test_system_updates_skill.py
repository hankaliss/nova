"""Tests for the system_updates skill (read-only, hermetic — pacman mocked)."""

import json
from pathlib import Path
from unittest.mock import patch

from nova.skills.loader import SkillLoader
from nova.tools.registry import ToolRegistry

SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"


def test_system_updates_skill_discovers():
    registry = ToolRegistry()
    loader = SkillLoader(SKILLS_DIR, registry=registry)
    loader.register_all(registry)

    assert "system_updates.check" in [t.name for t in registry.list_tools()]


@patch("subprocess.run")
def test_system_updates_check_returns_updates(mock_run):
    mock_run.return_value.returncode = 0
    mock_run.return_value.stdout = "linux 6.9.1-1\nfirefox 128.0-1\n"
    mock_run.return_value.stderr = ""

    registry = ToolRegistry()
    loader = SkillLoader(SKILLS_DIR, registry=registry)
    loader.register_all(registry)
    result = loader._tools(SKILLS_DIR / "system_updates")[0].implementation()

    assert result == {"count": 2, "updates": ["linux 6.9.1-1", "firefox 128.0-1"]}
    mock_run.assert_called_once_with(
        ["pacman", "-Qu"], capture_output=True, text=True, timeout=30
    )


@patch("subprocess.run")
def test_system_updates_check_reports_error(mock_run):
    mock_run.return_value.returncode = 1
    mock_run.return_value.stdout = ""
    mock_run.return_value.stderr = "error: could not open database\n"

    registry = ToolRegistry()
    loader = SkillLoader(SKILLS_DIR, registry=registry)
    loader.register_all(registry)
    result = loader._tools(SKILLS_DIR / "system_updates")[0].implementation()

    assert result == {"error": "error: could not open database"}


def test_skill_metadata_json():
    loader = SkillLoader(SKILLS_DIR)
    data = json.loads(loader.to_json())
    skill = next(s for s in data if s["name"] == "system_updates")

    assert skill["description"] == "System Updates"
    assert skill["tools"] == [{"name": "system_updates.check", "risk": "read"}]