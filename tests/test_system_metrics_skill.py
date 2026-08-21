"""Tests for the system_metrics skill (read-only, safe on any hardware)."""

import json
from pathlib import Path

from nova.skills.loader import SkillLoader
from nova.tools.registry import ToolRegistry

SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"


def test_system_metrics_skill_discovers():
    registry = ToolRegistry()
    loader = SkillLoader(SKILLS_DIR, registry=registry)
    loader.register_all(registry)

    assert "system_metrics.report" in [t.name for t in registry.list_tools()]


def test_system_metrics_report_generates_markdown():
    registry = ToolRegistry()
    loader = SkillLoader(SKILLS_DIR, registry=registry)
    loader.register_all(registry)
    tool = registry.get("system_metrics.report")

    result = tool.implementation()

    assert isinstance(result, dict)
    report = result["report"]
    assert report.startswith("# System Report")
    assert "## CPU" in report
    assert "## GPU" in report
    assert "## Memory" in report
    assert "## Disk (/)" in report
    assert "## Uptime" in report


def test_skill_metadata_json():
    loader = SkillLoader(SKILLS_DIR)
    data = json.loads(loader.to_json())
    sm = next(s for s in data if s["name"] == "system_metrics")

    assert sm["description"].startswith("Generates a Markdown report")
    assert sm["tools"] == [{"name": "system_metrics.report", "risk": "read"}]