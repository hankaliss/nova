"""Tests for MCP configuration loading/persistence."""

import json

from nova.mcp.config import (
    McpConfig,
    expand_env,
    load_config,
    upsert_server_entry,
    validate_server_entry,
    GITHUB_TOOLSETS,
    auto_configure_github,
)
from nova.tools.tool import RiskLevel


def _write(tmp_path, payload):
    path = tmp_path / "mcp.json"
    path.write_text(json.dumps(payload))
    return path


def test_missing_file_yields_empty_config(tmp_path):
    config, warnings = load_config(tmp_path / "nope.json")
    assert isinstance(config, McpConfig)
    assert config.servers == {}
    assert warnings == []


def test_valid_config_parses_with_defaults(tmp_path):
    path = _write(
        tmp_path,
        {
            "mcpServers": {
                "github": {
                    "command": "docker",
                    "args": ["run", "-i", "ghcr.io/github/github-mcp-server"],
                }
            }
        },
    )
    config, warnings = load_config(path)
    assert warnings == []
    server = config.servers["github"]
    assert server.command == "docker"
    # Defaults: enabled, 120s timeout.
    assert server.enabled is True
    assert server.timeout_s == 120


def test_malformed_json_warns_and_skips(tmp_path):
    path = tmp_path / "mcp.json"
    path.write_text("{not json")
    config, warnings = load_config(path)
    assert config.servers == {}
    assert len(warnings) == 1
    assert "cannot read" in warnings[0] or "Expecting" in warnings[0]


def test_invalid_entry_skipped_but_siblings_kept(tmp_path):
    path = _write(
        tmp_path,
        {
            "mcpServers": {
                "broken": {"args": ["no-command"]},
                "good": {"command": "echo-server"},
            }
        },
    )
    config, warnings = load_config(path)
    assert "good" in config.servers
    assert "broken" not in config.servers
    assert any("broken" in w for w in warnings)


def test_env_expansion_set_and_unset(monkeypatch):
    monkeypatch.setenv("MY_TOKEN", "abc123")
    expanded, warns = expand_env("token=${MY_TOKEN}!")
    assert expanded == "token=abc123!"
    assert warns == []

    expanded, warns = expand_env("${MISSING_VAR_X}")
    assert expanded == ""
    assert any("MISSING_VAR_X" in w for w in warns)


def test_env_expansion_applied_on_load(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_PAT", "secret")
    path = _write(
        tmp_path,
        {
            "mcpServers": {
                "github": {
                    "command": "srv",
                    "env": {"TOK": "${GITHUB_PAT}", "LIT": "plain"},
                }
            }
        },
    )
    config, warnings = load_config(path)
    assert config.servers["github"].env == {"TOK": "secret", "LIT": "plain"}


def test_legacy_risk_key_is_ignored(tmp_path):
    """Configs written before the field was removed still load."""
    path = tmp_path / "mcp.json"
    path.write_text(json.dumps({"mcpServers": {"x": {"command": "x", "risk": "read"}}}))
    config, warnings = load_config(path)
    assert warnings == []
    assert not hasattr(config.servers["x"], "risk")
    assert validate_server_entry({"command": "x", "risk": "banana"}).command == "x"


def test_upsert_adds_updates_and_removes_preserving_other_keys(tmp_path):
    path = tmp_path / "nested" / "mcp.json"
    upsert_server_entry("a", {"command": "cmd-a"}, path)
    upsert_server_entry("b", {"command": "cmd-b", "enabled": False}, path)
    raw = json.loads(path.read_text())
    assert set(raw["mcpServers"]) == {"a", "b"}

    upsert_server_entry("a", {"command": "cmd-a2"}, path)
    raw = json.loads(path.read_text())
    assert raw["mcpServers"]["a"]["command"] == "cmd-a2"

    # Unknown top-level keys survive round-trips.
    raw["futureKey"] = 1
    path.write_text(json.dumps(raw))
    upsert_server_entry("a", None, path)
    raw = json.loads(path.read_text())
    assert "a" not in raw["mcpServers"]
    assert raw["futureKey"] == 1


def test_auto_configure_github_returns_none_when_docker_missing(monkeypatch):
    import nova.mcp.config as config_module
    monkeypatch.setattr(config_module.shutil, "which", lambda _x: None)
    assert auto_configure_github() is None


def test_auto_configure_github_uses_oauth_without_pat(monkeypatch):
    """OAuth-first: baked-in app + loopback callback port, no token stored."""
    import json as _json

    import nova.mcp.config as config_module
    monkeypatch.setattr(
        config_module.shutil,
        "which",
        lambda x: "/usr/bin/docker" if x == "docker" else None,
    )

    result = auto_configure_github()

    assert result is not None
    assert result["command"] == "docker"
    assert "ghcr.io/github/github-mcp-server" in result["args"]
    # Loopback-only callback port matching the official app's redirect URL.
    assert "127.0.0.1:8085:8085" in result["args"]
    assert result["env"] == {"GITHUB_OAUTH_CALLBACK_PORT": "8085"}
    assert "GITHUB_PERSONAL_ACCESS_TOKEN" not in _json.dumps(result)
    # The server requires the `stdio` subcommand to launch on that transport.
    assert "stdio" in result["args"]
    assert "--oauth-callback-port" in result["args"]
    assert "8085" in result["args"]
    # AD-016: keep the tool surface small so schemas fit qwen3's context.
    assert "--toolsets" in result["args"]
    assert result["args"][result["args"].index("--toolsets") + 1] == GITHUB_TOOLSETS
    # AD-042: the extra toolsets reach skills only, not the model's prompt.
    assert set(result["skill_only"]) >= {"list_pull_requests", "list_issues", "actions_list"}
    assert result["enabled"] is True
    assert "risk" not in result


def test_auto_configure_github_with_pat(monkeypatch):
    """PAT mode: token is passed via env, no OAuth callback port mapped."""
    import nova.mcp.config as config_module
    monkeypatch.setattr(
        config_module.shutil,
        "which",
        lambda x: "/usr/bin/docker" if x == "docker" else None,
    )

    result = auto_configure_github(pat="ghp_fake123")

    assert result is not None
    assert "stdio" in result["args"]
    assert "127.0.0.1:8085:8085" not in result["args"]
    assert "--oauth-callback-port" not in result["args"]
    assert "--toolsets" in result["args"]
    assert result["args"][result["args"].index("--toolsets") + 1] == GITHUB_TOOLSETS
    assert result["env"] == {"GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_fake123"}
    assert "risk" not in result


def test_tool_risk_github_read_ops_are_read():
    from nova.mcp.manager import _tool_risk

    assert _tool_risk("github", "search_repositories") is RiskLevel.READ
    assert _tool_risk("github", "list_branches") is RiskLevel.READ
    assert _tool_risk("github", "get_file_contents") is RiskLevel.READ


def test_tool_risk_github_write_and_destructive():
    from nova.mcp.manager import _tool_risk

    assert _tool_risk("github", "push_files") is RiskLevel.WRITE
    assert _tool_risk("github", "create_repository") is RiskLevel.WRITE
    assert _tool_risk("github", "delete_file") is RiskLevel.DESTRUCTIVE


def test_tool_risk_unknown_server_uses_heuristics():
    from nova.mcp.manager import _tool_risk

    assert _tool_risk("other", "search_things") is RiskLevel.READ
    assert _tool_risk("other", "list_things") is RiskLevel.READ
    assert _tool_risk("other", "get_thing") is RiskLevel.READ
    assert _tool_risk("other", "create_thing") is RiskLevel.WRITE
    assert _tool_risk("other", "remove_thing") is RiskLevel.DESTRUCTIVE


def test_tool_risk_read_only_hint_beats_name():
    from types import SimpleNamespace

    from nova.mcp.manager import _tool_risk

    hint = SimpleNamespace(readOnlyHint=True)
    assert _tool_risk("github", "pull_request_read") is RiskLevel.WRITE
    assert _tool_risk("github", "pull_request_read", hint) is RiskLevel.READ
    assert _tool_risk("github", "actions_list", hint) is RiskLevel.READ
    # Curated overrides still win over the server's hint.
    assert _tool_risk("github", "push_files", hint) is RiskLevel.WRITE
    assert _tool_risk("github", "create_thing", SimpleNamespace(readOnlyHint=False)) is RiskLevel.WRITE
    # As the SDK delivers it (mcp.types.ToolAnnotations).
    from mcp.types import ToolAnnotations

    assert _tool_risk("github", "actions_list", ToolAnnotations(readOnlyHint=True)) is RiskLevel.READ


def test_skill_only_is_parsed_and_defaults_empty():
    entry = validate_server_entry({"command": "x", "skill_only": ["list_issues"]})
    assert entry.skill_only == ["list_issues"]
    assert validate_server_entry({"command": "x"}).skill_only == []
