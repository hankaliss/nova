"""Tests for the packages skill (read-only, hermetic: pacman and checkupdates mocked)."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from nova.skills.loader import SkillLoader
from nova.tools.registry import ToolRegistry

SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"


def _module():
    spec = importlib.util.spec_from_file_location("packages_tools", SKILLS_DIR / "packages" / "tools.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fake_run(updates_rc=0, updates="linux 6.9-1 -> 6.10-1\nfirefox 1-1 -> 2-1\n"):
    qei = "Name : big\nInstalled Size : 2.00 GiB\n\nName : small\nInstalled Size : 10.00 KiB\n"
    qi = "Name : orphan\nInstalled Size : 1.00 MiB\n"

    def run(cmd, **_):
        if cmd[0] == "checkupdates":
            return SimpleNamespace(returncode=updates_rc, stdout=updates, stderr="boom" if updates_rc == 1 else "")
        if cmd[:2] == ["pacman", "-Qei"]:
            return SimpleNamespace(returncode=0, stdout=qei, stderr="")
        if cmd[:2] == ["pacman", "-Qdtq"]:
            return SimpleNamespace(returncode=0, stdout="orphan\n", stderr="")
        return SimpleNamespace(returncode=0, stdout=qi, stderr="")

    return run


def test_skill_discovers_and_is_read_only():
    registry = ToolRegistry()
    loader = SkillLoader(SKILLS_DIR, registry=registry)
    loader.register_all(registry)
    assert "packages.report" in [t.name for t in registry.list_tools()]
    data = json.loads(SkillLoader(SKILLS_DIR).to_json())
    skill = next(s for s in data if s["name"] == "packages")
    assert skill["tools"] == [{"name": "packages.report", "risk": "read"}]
    assert not any(s["name"] == "system_updates" for s in data)


def test_report_summarises_updates_orphans_and_big_packages(tmp_path):
    mod = _module()
    log = tmp_path / "pacman.log"
    log.write_text(
        "[2026-10-01T10:00:00+0200] [ALPM] installed foo (1.0-1)\n"
        "[2026-10-02T10:00:00+0200] [PACMAN] Running 'pacman -Syu'\n"
        "[2026-10-03T10:00:00+0200] [ALPM] upgraded bar (1.0-1 -> 1.1-1)\n"
    )
    with patch.object(mod, "PACMAN_LOG", log), patch.object(mod.shutil, "which", return_value="/usr/bin/x"), \
            patch.object(mod, "_run", _fake_run()):
        result = mod._report()

    assert result["pending_count"] == 2
    assert result["needs_attention"] == ["linux"]
    assert result["largest_explicit"][0] == "big: 2.0 GB"
    assert result["orphans"] == ["orphan"] and result["orphans_size"] == "1.0 MB"
    assert result["recent_changes"][0].startswith("2026-10-03 upgraded bar")
    assert "artifact" in json.dumps(result)


def test_recent_parses_only_alpm_package_lines(tmp_path):
    mod = _module()
    log = tmp_path / "pacman.log"
    log.write_text(
        "[2026-10-01T10:00:00+0200] [ALPM] installed foo (1.0-1)\n"
        "[2026-10-02T10:00:00+0200] [ALPM] transaction started\n"
        "[2026-10-03T10:00:00+0200] [ALPM] upgraded bar (1.0-1 -> 1.1-1)\n"
    )
    assert mod._recent(log) == [
        ("2026-10-03", "upgraded", "bar", "1.0-1 -> 1.1-1"),
        ("2026-10-01", "installed", "foo", "1.0-1"),
    ]
    assert mod._recent(tmp_path / "missing.log") == []


def test_no_updates_is_not_an_error():
    mod = _module()
    with patch.object(mod.shutil, "which", return_value="/usr/bin/x"), patch.object(mod, "_run", _fake_run(2, "")):
        assert mod._pending() == ([], "")


def test_checkupdates_failure_and_missing_are_reported_not_raised():
    mod = _module()
    with patch.object(mod.shutil, "which", return_value="/usr/bin/x"), patch.object(mod, "_run", _fake_run(1, "")):
        assert mod._pending() == ([], "boom")
    with patch.object(mod.shutil, "which", return_value=None):
        assert "pacman-contrib" in mod._pending()[1]


def test_installed_sizes_accept_decimal_comma():
    mod = _module()
    out = SimpleNamespace(returncode=0, stdout="Name : a\nInstalled Size : 1,50 KiB\n", stderr="")
    with patch.object(mod, "_run", lambda *a, **k: out):
        assert mod._installed(["-Qi"]) == [("a", 1536)]
