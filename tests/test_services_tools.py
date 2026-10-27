"""Tests for the systemd service tools (subprocess faked)."""

import json
from types import SimpleNamespace

import pytest

from nova.tools import services
from nova.tools.registry import ToolRegistry
from nova.tools.services import ServiceError
from nova.tools.tool import RiskLevel


@pytest.fixture
def calls(monkeypatch):
    """Record argv lists; answer like systemctl/journalctl."""
    recorded = []

    def fake_run(cmd, **kw):
        recorded.append(cmd)
        out = ""
        if "list-units" in cmd:
            out = json.dumps([{"unit": "syncthing.service", "load": "loaded", "active": "active",
                               "sub": "running", "description": "Syncthing"}])
        elif "show" in cmd:
            out = "Id=syncthing.service\nLoadState=loaded\nActiveState=active\nSubState=running\n"
        elif cmd[0] == "journalctl":
            out = "line1\nline2\n"
        elif "is-active" in cmd:
            out = "active\n"
        return SimpleNamespace(returncode=0, stdout=out, stderr="")

    monkeypatch.setattr(services.subprocess, "run", fake_run)
    return recorded


def test_list_both_scopes_by_default(calls):
    result = services._list()
    assert [s["scope"] for s in result["services"]] == ["user", "system"]
    assert calls[0] == ["systemctl", "--user", "list-units", "--type=service",
                        "--state=running", "--output=json", "--no-pager"]
    assert calls[1][:2] == ["systemctl", "list-units"]


def test_list_system_failed(calls):
    services._list("system", "failed")
    assert len(calls) == 1
    assert calls[0][:2] == ["systemctl", "list-units"] and "--state=failed" in calls[0]


def test_list_match(calls):
    assert len(services._list("user", match="SYNC")["services"]) == 1
    assert services._list("user", match="ollama")["services"] == []


def test_status_with_logs(calls):
    result = services._status("syncthing", log_lines=5)
    assert result["ActiveState"] == "active" and result["log"] == ["line1", "line2"]
    assert calls[0][3] == "syncthing.service"
    assert calls[1][:2] == ["journalctl", "--user-unit=syncthing.service"]


def test_status_system_scope_uses_unit_flag(calls):
    services._status("ollama", scope="system", log_lines=3)
    assert calls[1][1] == "--unit=ollama.service"


def test_status_auto_falls_back_to_system(monkeypatch):
    seen = []

    def fake_run(cmd, **kw):
        seen.append(cmd)
        if cmd[:2] == ["systemctl", "--user"]:
            return SimpleNamespace(returncode=0, stdout="LoadState=not-found\n", stderr="")
        if cmd[0] == "systemctl":
            return SimpleNamespace(returncode=0, stdout="Id=ollama.service\nLoadState=loaded\nActiveState=active\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(services.subprocess, "run", fake_run)
    result = services._status("ollama", log_lines=0)
    assert result["scope"] == "system" and result["ActiveState"] == "active"
    assert [c[:2] for c in seen] == [["systemctl", "--user"], ["systemctl", "show"]]


def test_status_not_found(monkeypatch):
    monkeypatch.setattr(services.subprocess, "run", lambda cmd, **kw: SimpleNamespace(
        returncode=0, stdout="Id=x.service\nLoadState=not-found\n", stderr=""))
    with pytest.raises(ServiceError, match="not found \\(user or system"):
        services._status("x")


@pytest.mark.parametrize("name", ["", "-rf /", "foo;rm -rf ~", "a b", "$(reboot)", "../etc"])
def test_invalid_names_never_run(calls, name):
    with pytest.raises(ServiceError, match="invalid unit name"):
        services._status(name)
    assert calls == []


def test_bad_scope_and_state(calls):
    with pytest.raises(ServiceError):
        services._list(scope="root")
    with pytest.raises(ServiceError):
        services._list(state="dead")


def test_control_user_scope_only(calls):
    result = services._control("restart", "syncthing")
    assert calls[0] == ["systemctl", "--user", "restart", "syncthing.service", "--no-pager"]
    assert result == {"unit": "syncthing.service", "action": "restart", "active": "active"}


@pytest.mark.parametrize("action, unit", [
    ("stop", "plasma-kwin_wayland"),
    ("restart", "plasma-kwin_wayland"),
    ("stop", "plasma-plasmashell"),
    ("stop", "pipewire"),
    ("restart", "dbus-broker"),
    ("stop", "xdg-desktop-portal-kde"),
])
def test_session_units_refused(calls, action, unit):
    with pytest.raises(ServiceError, match="desktop session"):
        services._control(action, unit)
    assert calls == []


def test_plasmashell_restart_allowed(calls):
    services._control("restart", "plasma-plasmashell")
    assert calls[0][2:4] == ["restart", "plasma-plasmashell.service"]


def test_risk_levels():
    tools = {t.name: t for t in services.get_tools()}
    assert tools["services.list"].risk == RiskLevel.READ
    assert tools["services.status"].risk == RiskLevel.READ
    for action in ("start", "stop", "restart"):
        tool = tools[f"services.{action}"]
        assert tool.risk == RiskLevel.WRITE and tool.auto_approvable is False


@pytest.mark.asyncio
async def test_control_needs_approval(calls):
    registry = ToolRegistry()
    registry.register_service_tools()
    registry.permissions.auto_approve_writes = True  # still asks: not auto-approvable
    result = await registry.execute("services.stop", {"name": "syncthing"})
    assert not result.success
    assert calls == []
