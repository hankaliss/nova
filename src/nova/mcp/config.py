"""MCP server configuration.

Servers are declared in ``~/.config/nova/mcp.json`` using a
Claude-Desktop-compatible layout plus Nova-specific fields:

    {
      "mcpServers": {
        "github": {
          "command": "docker",
          "args": [
            "run", "-i", "--rm",
            "-p", "127.0.0.1:8085:8085",
            "-e", "GITHUB_OAUTH_CALLBACK_PORT",
            "ghcr.io/github/github-mcp-server",
            "stdio", "--oauth-callback-port", "8085"
          ],
          "env": {"GITHUB_OAUTH_CALLBACK_PORT": "8085"},
          "enabled": true
        }
      }
    }

Each tool's risk comes from Nova (``nova.mcp.manager._tool_risk``: curated
overrides, then the tool name), never from this file. Older configs may
still contain a ``"risk"`` key; it is ignored.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError


_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def expand_env(value: str) -> tuple[str, list[str]]:
    """Expand ``${VAR}`` references from the process environment.

    Returns the expanded string and a list of warnings for variables
    that are not set (they expand to an empty string).
    """
    warnings: list[str] = []

    def _sub(match: re.Match[str]) -> str:
        name = match.group(1)
        if name in os.environ:
            return os.environ[name]
        warnings.append(f"environment variable '{name}' is not set")
        return ""

    return _ENV_RE.sub(_sub, value), warnings


class McpServerConfig(BaseModel):
    """One MCP server process."""

    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    cwd: str | None = None
    enabled: bool = True
    timeout_s: int = Field(default=120, ge=1, le=600)
    # Tools (server's own names) kept out of the model's prompt; READ ones
    # stay callable by skills (AD-042).
    skill_only: list[str] = Field(default_factory=list)


class McpConfig(BaseModel):
    """The full set of configured servers."""

    servers: dict[str, McpServerConfig] = Field(default_factory=dict)


def default_config_path() -> Path:
    override = os.environ.get("NOVA_MCP_CONFIG")
    if override:
        return Path(override)
    return Path.home() / ".config" / "nova" / "mcp.json"


def load_config(path: Path | None = None) -> tuple[McpConfig, list[str]]:
    """Load and validate the config file.

    A missing file yields an empty configuration without warnings.
    Malformed JSON or invalid server entries produce a warning each and
    are skipped so one bad entry cannot take down startup.
    """
    path = path or default_config_path()
    if not path.exists():
        return McpConfig(), []

    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        return McpConfig(), [f"cannot read {path}: {e}"]

    warnings: list[str] = []
    servers_raw = raw.get("mcpServers")
    if not isinstance(servers_raw, dict):
        return McpConfig(), ["config has no 'mcpServers' object"]

    servers: dict[str, McpServerConfig] = {}
    for name, entry in servers_raw.items():
        # Expand ${VAR} in env values before validation.
        if isinstance(entry, dict) and isinstance(entry.get("env"), dict):
            entry = dict(entry)
            expanded_env: dict[str, str] = {}
            for key, value in entry["env"].items():
                if isinstance(value, str):
                    expanded, warns = expand_env(value)
                    for w in warns:
                        warnings.append(f"[{name}] {w}")
                    expanded_env[key] = expanded
                else:
                    expanded_env[key] = value
            entry["env"] = expanded_env
        try:
            servers[name] = McpServerConfig(**entry)
        except ValidationError as e:
            warnings.append(f"[{name}] skipped invalid entry: {e.json(indent=None)}")

    return McpConfig(servers=servers), warnings


def _write_raw(payload: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n")
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0600
    except OSError:
        pass


def validate_server_entry(entry: dict[str, Any]) -> McpServerConfig:
    """Validate a raw JSON entry (as typed by the user in the UI)."""
    return McpServerConfig(**entry)


def upsert_server_entry(
    name: str,
    entry: dict[str, Any] | None,
    path: Path | None = None,
) -> None:
    """Insert/update/remove one server entry while preserving the rest
    of the file (unknown fields included). ``entry=None`` removes it."""
    path = path or default_config_path()
    if path.exists():
        try:
            raw = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            raw = {}
    else:
        raw = {}
    if not isinstance(raw.get("mcpServers"), dict):
        raw["mcpServers"] = {}
    servers = raw["mcpServers"]
    if entry is None:
        servers.pop(name, None)
    else:
        servers[name] = entry
    _write_raw(raw, path)


GITHUB_TOOLSETS = "repos,users,context,issues,pull_requests,actions"

# Every tool of the issues, pull_requests and actions toolsets
# (github-mcp-server 1.10). Write tools among them are hidden too: skills
# can't call them and the model never asked for them before.
GITHUB_SKILL_ONLY = (
    "add_issue_comment", "get_label", "issue_read", "issue_write", "list_issue_fields",
    "list_issue_types", "list_issues", "search_issues", "sub_issue_write",
    "add_comment_to_pending_review", "add_reply_to_pull_request_comment", "create_pull_request",
    "list_pull_requests", "merge_pull_request", "pull_request_read", "pull_request_review_write",
    "search_pull_requests", "update_pull_request", "update_pull_request_branch",
    "actions_get", "actions_list", "actions_run_trigger", "get_job_logs",
)


def auto_configure_github(*, pat: str | None = None) -> dict[str, Any] | None:
    """Return the default GitHub MCP server entry.

    Official images ship with a baked-in GitHub OAuth app, so no personal
    access token is needed by default: on the first tool call the server
    walks the user through GitHub authorization in the browser.  The fixed
    callback port 8085 is published to loopback only — it matches the
    redirect URL registered for the official app.

    When *pat* is provided, the server authenticates via that token
    (``GITHUB_PERSONAL_ACCESS_TOKEN``) and no OAuth flow is needed.
    The callback port mapping is skipped in PAT mode.

    Returns None when docker is unavailable.
    """
    if shutil.which("docker") is None:
        return None

    port = "8085"
    args = [
        "run", "-i", "--rm",
    ]
    env: dict[str, str] = {}

    if pat:
        env["GITHUB_PERSONAL_ACCESS_TOKEN"] = pat
    else:
        args += ["-p", f"127.0.0.1:{port}:{port}"]
        env["GITHUB_OAUTH_CALLBACK_PORT"] = port

    args += [
        "-e", "GITHUB_PERSONAL_ACCESS_TOKEN" if pat else "GITHUB_OAUTH_CALLBACK_PORT",
        "ghcr.io/github/github-mcp-server",
        "stdio",
        # Keep the model's tool surface small (AD-016): repos+users+context
        # cover repo listing/search and get_me. issues, pull_requests and
        # actions feed the git_companion report and are skill_only (AD-042),
        # so they add nothing to the prompt.
        "--toolsets", GITHUB_TOOLSETS,
    ]

    if not pat:
        args += ["--oauth-callback-port", port]

    return {
        "command": "docker",
        "args": args,
        "env": env,
        "enabled": True,
        "skill_only": list(GITHUB_SKILL_ONLY),
    }


