"""systemd service tools for Nova (AD-036).

Listing services and reading a unit's status and recent journal are READ.
start / stop / restart are WRITE, for user services only (system services
need root), and ask every time (not covered by auto-approve). Units that
hold the desktop session together are refused outright: stopping KWin from
an approval dialog would end the session.

All calls are structured argv lists, never a shell; unit names are
validated first.
"""

import json
import re
import subprocess
from typing import Any

from nova.tools.tool import RiskLevel, Tool

TIMEOUT = 30
MAX_UNITS = 100
MAX_LOG_LINES = 100
_UNIT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9@._:\\-]{0,200}$")
_SCOPES = ("user", "system")
_STATES = {"running": ["--state=running"], "failed": ["--state=failed"], "all": ["--all"]}
_PROPERTIES = (
    "Id,Description,LoadState,ActiveState,SubState,UnitFileState,MainPID,"
    "ExecMainStartTimestamp,MemoryCurrent,NRestarts,Result,FragmentPath"
)
# The desktop session: never stopped or restarted by the model.
_PROTECTED = re.compile(
    r"^(plasma-.*|kwin.*|pipewire.*|wireplumber.*|dbus.*|xdg-desktop-portal.*"
    r"|at-spi.*|systemd-.*|graphical-session.*|ssh-agent.*|gpg-agent.*)\.service$"
)
# ...except restarting the panel, the usual fix for a frozen plasmashell.
_RESTART_OK = {"plasma-plasmashell.service"}


class ServiceError(Exception):
    """Raised when a unit name or scope is invalid, or systemctl fails."""


def _unit(name: str) -> str:
    name = (name or "").strip()
    if not _UNIT.match(name):
        raise ServiceError(f"invalid unit name: {name!r}")
    return name if name.endswith(".service") else f"{name}.service"


def _scope_args(scope: str) -> list[str]:
    if scope not in _SCOPES:
        raise ServiceError("scope must be 'user' or 'system'")
    return ["--user"] if scope == "user" else []


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT)
    except FileNotFoundError as e:
        raise ServiceError(f"{cmd[0]} not found") from e
    except subprocess.TimeoutExpired as e:
        raise ServiceError(f"{cmd[0]} timed out") from e


def _list_scope(scope: str, state: str) -> list[dict[str, str]]:
    cmd = ["systemctl", *_scope_args(scope), "list-units", "--type=service",
           *_STATES[state], "--output=json", "--no-pager"]
    result = _run(cmd)
    if result.returncode != 0:
        raise ServiceError(result.stderr.strip() or "systemctl failed")
    return [
        {"scope": scope, **{k: u.get(k, "") for k in ("unit", "active", "sub", "description")}}
        for u in json.loads(result.stdout or "[]")
    ]


def _list(scope: str = "all", state: str = "running", match: str = "") -> dict[str, Any]:
    """``scope="all"`` lists user and system services together: a small
    model that looked only at user services reported a running system
    service as missing."""
    if state not in _STATES:
        raise ServiceError("state must be 'running', 'failed' or 'all'")
    scopes = _SCOPES if scope == "all" else (scope,)
    services = [svc for sc in scopes for svc in _list_scope(sc, state)]
    if match:
        needle = match.lower()
        services = [
            svc for svc in services
            if needle in svc["unit"].lower() or needle in svc["description"].lower()
        ]
    return {
        "scope": scope,
        "state": state,
        "services": services[:MAX_UNITS],
        "truncated": len(services) > MAX_UNITS,
    }


def _status(name: str, scope: str = "auto", log_lines: int = 20) -> dict[str, Any]:
    """``scope="auto"`` looks in the user's services, then the system's.

    Small models rarely pass a scope; a system service looked up only among
    user services came back "not found" and was reported as not running.
    """
    if scope == "auto":
        try:
            return _status(name, "user", log_lines)
        except ServiceError as e:
            if "not found" not in str(e):
                raise
        try:
            return _status(name, "system", log_lines)
        except ServiceError as e:
            if "not found" in str(e):
                raise ServiceError(f"{_unit(name)} not found (user or system services)") from e
            raise
    unit = _unit(name)
    scope_args = _scope_args(scope)
    log_lines = max(0, min(int(log_lines), MAX_LOG_LINES))
    result = _run(["systemctl", *scope_args, "show", unit,
                   f"--property={_PROPERTIES}", "--no-pager"])
    if result.returncode != 0:
        raise ServiceError(result.stderr.strip() or "systemctl failed")
    info = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    if info.get("LoadState") == "not-found":
        raise ServiceError(f"{unit} not found ({scope} services)")
    status: dict[str, Any] = {"scope": scope, **info}
    if log_lines:
        unit_flag = "--user-unit" if scope == "user" else "--unit"
        logs = _run(["journalctl", f"{unit_flag}={unit}", "-n", str(log_lines),
                     "--no-pager", "-o", "short-iso"])
        status["log"] = logs.stdout.strip().splitlines()[-log_lines:]
    return status


def _control(action: str, name: str) -> dict[str, Any]:
    unit = _unit(name)
    if _PROTECTED.match(unit) and not (action == "restart" and unit in _RESTART_OK):
        raise ServiceError(
            f"{unit} is part of the desktop session; Nova won't {action} it"
        )
    result = _run(["systemctl", "--user", action, unit, "--no-pager"])
    if result.returncode != 0:
        raise ServiceError(result.stderr.strip() or f"systemctl {action} failed")
    # is-active also answers for a unit that unloaded when it stopped.
    after = _run(["systemctl", "--user", "is-active", unit])
    return {"unit": unit, "action": action, "active": after.stdout.strip()}


_NAME = {"type": "string", "description": "Unit name, e.g. syncthing or syncthing.service"}


def _control_tool(action: str, description: str) -> Tool:
    return Tool(
        name=f"services.{action}",
        description=description,
        parameters={"type": "object", "properties": {"name": _NAME}, "required": ["name"]},
        risk=RiskLevel.WRITE,
        permission=f"services.{action}",
        auto_approvable=False,
        implementation=lambda name: _control(action, name),
    )


def get_tools() -> list[Tool]:
    return [
        Tool(
            name="services.list",
            description=(
                "List systemd services (user and system by default). state: "
                "running (default), failed or all; match filters by name or "
                "description. To check one service, use services.status."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "scope": {
                        "type": "string",
                        "enum": ["all", *_SCOPES],
                        "description": "Default all",
                    },
                    "state": {"type": "string", "enum": list(_STATES), "description": "Default running"},
                    "match": {"type": "string", "description": "Only services whose name or description contains this"},
                },
            },
            risk=RiskLevel.READ,
            permission="services.read",
            implementation=_list,
        ),
        Tool(
            name="services.status",
            description=(
                "Status of one systemd service (active state, PID, memory, "
                "restarts, last result) and its recent journal lines."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": _NAME,
                    "scope": {
                        "type": "string",
                        "enum": ["auto", *_SCOPES],
                        "description": "Default auto: user services, then system",
                    },
                    "log_lines": {"type": "integer", "description": "Default 20, max 100, 0 = none"},
                },
                "required": ["name"],
            },
            risk=RiskLevel.READ,
            permission="services.read",
            implementation=_status,
        ),
        _control_tool("start", "Start one of the user's systemd user services. Asks the user first."),
        _control_tool("stop", "Stop one of the user's systemd user services. Asks the user first."),
        _control_tool(
            "restart",
            "Restart one of the user's systemd user services (e.g. a frozen "
            "plasma-plasmashell). Asks the user first.",
        ),
    ]
