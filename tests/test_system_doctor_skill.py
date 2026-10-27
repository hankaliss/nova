"""System doctor skill (issue #12): fake journal/systemctl/coredumpctl output."""

import json
from pathlib import Path

import pytest

from nova.skills.loader import SkillLoader
from nova.tools.registry import ToolRegistry
from nova.tools.report import ARTIFACT_KEY

SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"


def load_tool():
    registry = ToolRegistry()
    SkillLoader(SKILLS_DIR, registry=registry).register_all(registry)
    return registry.get("system_doctor.check")


def entry(msg, unit="", ident="", **extra):
    e = {"MESSAGE": msg, "SYSLOG_IDENTIFIER": ident}
    if unit:
        e["_SYSTEMD_UNIT"] = unit
    e.update(extra)
    return json.dumps(e)


ERRORS = "\n".join([
    entry("Invalid framebuffer status 0x8cd6", unit="user@1000.service", _SYSTEMD_USER_UNIT="plasma-kwin_wayland.service"),
    entry("Invalid framebuffer status 0x8cd7", _SYSTEMD_USER_UNIT="plasma-kwin_wayland.service"),
    entry("pam_unix(sudo:auth): conversation failed", ident="sudo"),
    entry(None, unit="systemd-coredump@0-1-13449_10516-0.service"),
    entry("Out of memory: Killed process 4242 (firefox) total-vm:123kB", ident="kernel"),
])
KERNEL = "\n".join([
    entry("[UFW BLOCK] IN=eno1 OUT= MAC=01:00 SRC=192.168.1.254 DST=224.0.0.1", ident="kernel"),
    entry("[UFW BLOCK] IN=eno1 OUT= MAC=01:00 SRC=192.168.1.9 DST=224.0.0.1", ident="kernel"),
    entry("nvidia: module verification failed", ident="kernel"),
])
BOOTS = json.dumps([{"index": 0, "first_entry": 1_792_000_000_000_000, "last_entry": 1_792_003_600_000_000}])
COREDUMPS = json.dumps([
    {"time": 1_792_000_100_000_000, "sig": 11, "exe": "/usr/bin/foo"},
    {"time": 1_792_000_200_000_000, "sig": 11, "exe": "/usr/bin/foo"},
    {"time": 1_792_000_300_000_000, "sig": 6, "exe": "/usr/bin/bar"},
])
FAILED_SYSTEM = json.dumps([{"unit": "bluetooth.service", "description": "Bluetooth service"}])


@pytest.fixture
def fake_system(monkeypatch):
    tool = load_tool()
    module = tool.implementation.__globals__  # the loader's private module
    calls = []

    def fake_run(cmd, timeout=20):
        calls.append(cmd)
        if cmd[0] == "journalctl" and "--list-boots" in cmd:
            return BOOTS
        if cmd[0] == "journalctl" and "-k" in cmd:
            return KERNEL
        if cmd[0] == "journalctl":
            return ERRORS
        if cmd[0] == "systemctl":
            return "[]" if "--user" in cmd else FAILED_SYSTEM
        if cmd[0] == "coredumpctl":
            return COREDUMPS
        if cmd[0] == "systemd-analyze":
            return "Startup finished in 8.3s (firmware) + 21.0s (userspace) = 1min 4.414s \n"
        return ""

    monkeypatch.setitem(module, "_run", fake_run)
    monkeypatch.setattr(module["shutil"], "which", lambda name: "/usr/bin/" + name)
    return tool, calls


def test_discovered_with_description():
    data = json.loads(SkillLoader(SKILLS_DIR).to_json())
    doctor = next(s for s in data if s["name"] == "system_doctor")
    assert doctor["description"].startswith("Finds what went wrong")
    assert doctor["tools"] == [{"name": "system_doctor.check", "risk": "read"}]
    assert "system_metrics" not in [s["name"] for s in data]


def test_check_summarizes_and_renders(fake_system):
    tool, calls = fake_system
    result = tool.implementation()
    report = result.pop(ARTIFACT_KEY)

    assert result["failed_units"] == ["bluetooth.service (system)"]
    assert result["errors"] == 5
    sources = {s["source"]: s["count"] for s in result["error_sources"]}
    assert sources["plasma-kwin_wayland"] == 2  # same message, numbers ignored
    assert sources["systemd-coredump"] == 1  # template instance folded
    assert result["kernel_warnings"] == 1  # firewall lines not counted
    assert result["firewall_blocked_packets"] == 2
    assert result["oom_kills"] == 1
    assert result["crashes"][0].startswith("foo ×2 (SIGSEGV")
    assert result["boot_time"] == "1 min 4 s"
    assert "None" not in json.dumps(result)
    assert len(json.dumps(result)) < 3000  # compact for the model

    assert report["title"] == "System doctor"
    html = report["html"]
    for text in ("bluetooth.service", "Errors by source", "Firewall blocked 2 packets", "SIGSEGV"):
        assert text in html
    assert all(c[0] in ("journalctl", "systemctl", "coredumpctl", "systemd-analyze") for c in calls)


def test_previous_boot_skips_live_state(fake_system):
    tool, calls = fake_system
    result = tool.implementation(boot=-1)
    assert result["boot"] == "the previous boot"
    assert result["failed_units"] == [] and result["boot_time"] == ""
    assert not any(c[0] in ("systemctl", "systemd-analyze") for c in calls)
    assert any(c[:3] == ["journalctl", "-b", "-1"] for c in calls)


def test_all_clear(monkeypatch, fake_system):
    tool, _ = fake_system
    module = tool.implementation.__globals__
    monkeypatch.setitem(module, "_run", lambda cmd, timeout=20: "[]" if cmd[0] in ("systemctl", "coredumpctl") else "")
    result = tool.implementation()
    assert result["errors"] == 0 and result["crashes"] == []
    assert "All clear." in result[ARTIFACT_KEY]["html"]


def test_refuses_without_journal(monkeypatch):
    tool = load_tool()
    monkeypatch.setattr(tool.implementation.__globals__["shutil"], "which", lambda name: None)
    with pytest.raises(ValueError, match="journalctl"):
        tool.implementation()
