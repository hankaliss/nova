"""Tests for the disk_cleaner skill (hermetic: fake HOME, no pacman)."""

from pathlib import Path

import pytest

from nova.skills.loader import SkillLoader
from nova.tools.registry import ToolRegistry
from nova.tools.tool import RiskLevel

SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"


@pytest.fixture
def tools(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    loader = SkillLoader(SKILLS_DIR)
    mod = {t.name: t for t in loader._tools(SKILLS_DIR / "disk_cleaner")}
    skill = mod["disk_cleaner.scan"].implementation.__globals__
    monkeypatch.setitem(skill, "_OLLAMA_DIRS", (tmp_path / ".ollama" / "models",))
    monkeypatch.setitem(skill, "_pacman_cache", lambda: (0, "1.0 GB"))
    return mod


def _fill(path: Path, size: int):
    path.mkdir(parents=True, exist_ok=True)
    (path / "blob").write_bytes(b"x" * size)


def test_registered_with_risks():
    registry = ToolRegistry()
    SkillLoader(SKILLS_DIR, registry=registry).register_all(registry)
    by_name = {t.name: t for t in registry.list_tools()}
    assert by_name["disk_cleaner.scan"].risk == RiskLevel.READ
    clear = by_name["disk_cleaner.clear_cache"]
    assert clear.risk == RiskLevel.WRITE and clear.auto_approvable is False


def test_scan_reports_folders_caches_and_modules(tools, tmp_path):
    _fill(tmp_path / "big", 200_000)
    _fill(tmp_path / ".cache" / "mesa", 100_000)
    _fill(tmp_path / ".ollama" / "models", 50_000)
    _fill(tmp_path / "Projects" / "app" / "node_modules", 30_000)

    result = tools["disk_cleaner.scan"].implementation()

    assert any(f.startswith(str(tmp_path / "big")) for f in result["largest_folders"])
    assert result["largest_caches"][0].startswith("mesa")
    assert result["ollama_models"].startswith(str(tmp_path / ".ollama" / "models"))
    assert result["node_modules"]["count"] == 1
    assert result["old_package_versions_reclaimable"] == "1.0 GB"
    assert "artifact" in str(result).lower()


def test_scan_rejects_missing_folder(tools, tmp_path):
    with pytest.raises(ValueError):
        tools["disk_cleaner.scan"].implementation(path=str(tmp_path / "nope"))


def test_clear_cache_empties_folder_but_keeps_it(tools, tmp_path):
    _fill(tmp_path / ".cache" / "mesa", 100_000)
    (tmp_path / ".cache" / "mesa" / "sub").mkdir()
    (tmp_path / ".cache" / "keep").mkdir()

    result = tools["disk_cleaner.clear_cache"].implementation("mesa")

    assert result["entries_failed"] == 0
    assert (tmp_path / ".cache" / "mesa").is_dir()
    assert list((tmp_path / ".cache" / "mesa").iterdir()) == []
    assert (tmp_path / ".cache" / "keep").is_dir()


@pytest.mark.parametrize("name", ["", "..", "../Documents", "a/b", "missing"])
def test_clear_cache_refuses_bad_names(tools, tmp_path, name):
    (tmp_path / "Documents").mkdir()
    with pytest.raises(ValueError):
        tools["disk_cleaner.clear_cache"].implementation(name)
    assert (tmp_path / "Documents").is_dir()


def test_clear_cache_refuses_symlink(tools, tmp_path):
    (tmp_path / "Documents").mkdir()
    (tmp_path / "Documents" / "f").write_text("x")
    (tmp_path / ".cache").mkdir()
    (tmp_path / ".cache" / "link").symlink_to(tmp_path / "Documents")

    with pytest.raises(ValueError):
        tools["disk_cleaner.clear_cache"].implementation("link")
    assert (tmp_path / "Documents" / "f").exists()
