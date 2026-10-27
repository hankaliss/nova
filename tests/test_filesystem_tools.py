"""Tests for the filesystem READ tools and their path policy (AD-030)."""

import pytest

from nova.tools import filesystem
from nova.tools.filesystem import FilesystemError, PathPolicy
from nova.tools.registry import ToolRegistry
from nova.tools.tool import RiskLevel


@pytest.fixture
def home(tmp_path):
    home = tmp_path / "home"
    (home / "notes").mkdir(parents=True)
    (home / "notes" / "todo.txt").write_text("buy milk\nfix nova\nsleep\n")
    (home / "notes" / "ideas.md").write_text("# Ideas\nglobal shortcut\n")
    (home / "project" / "node_modules" / "x").mkdir(parents=True)
    (home / "project" / "node_modules" / "x" / "todo.txt").write_text("fix nova\n")
    (home / "project" / "main.py").write_text("print('fix nova')\n")
    (home / "project" / ".env").write_text("TOKEN=secret\n")
    (home / ".ssh").mkdir()
    (home / ".ssh" / "id_ed25519").write_text("PRIVATE KEY\n")
    (home / ".bashrc").write_text("alias ll='ls -l'\n")
    (home / "id_rsa").write_text("PRIVATE KEY\n")
    (home / "server.pem").write_text("CERT\n")
    (home / ".local" / "share" / "nova").mkdir(parents=True)
    (home / ".local" / "share" / "nova" / "nova.db").write_bytes(b"SQLite\0")
    return home


@pytest.fixture
def policy(home):
    return PathPolicy(home)


# --- policy ---------------------------------------------------------------


def test_policy_resolves_tilde_and_relative(policy, home):
    assert policy.check("~/notes/todo.txt") == home / "notes" / "todo.txt"
    assert policy.check("notes") == home / "notes"


def test_policy_refuses_outside_home(policy, tmp_path):
    (tmp_path / "outside.txt").write_text("x")
    with pytest.raises(FilesystemError, match="outside the home"):
        policy.check(str(tmp_path / "outside.txt"))
    with pytest.raises(FilesystemError, match="outside the home"):
        policy.check("/etc/passwd")
    with pytest.raises(FilesystemError, match="outside the home"):
        policy.check("~/../outside.txt")


@pytest.mark.parametrize(
    "path",
    [
        "~/.ssh",
        "~/.ssh/id_ed25519",
        "~/id_rsa",
        "~/server.pem",
        "~/project/.env",
        "~/.local/share/nova/nova.db",
    ],
)
def test_policy_blocks_secrets(policy, path):
    with pytest.raises(FilesystemError, match="blocked"):
        policy.check(path)


def test_policy_follows_symlinks(policy, home, tmp_path):
    (tmp_path / "outside.txt").write_text("x")
    (home / "escape").symlink_to(tmp_path / "outside.txt")
    (home / "keys").symlink_to(home / ".ssh")
    with pytest.raises(FilesystemError, match="outside the home"):
        policy.check("~/escape")
    with pytest.raises(FilesystemError, match="blocked"):
        policy.check("~/keys/id_ed25519")


def test_policy_requires_path(policy):
    with pytest.raises(FilesystemError, match="required"):
        policy.check("  ")


# --- list -----------------------------------------------------------------


def test_list_hides_dotfiles_and_secrets(policy):
    result = filesystem._list(policy, "~")
    names = [e["name"] for e in result["entries"]]
    assert names == ["notes", "project"]
    assert result["path"] == "~"


def test_list_show_hidden_still_skips_secrets(policy):
    names = [e["name"] for e in filesystem._list(policy, "~", show_hidden=True)["entries"]]
    assert ".bashrc" in names and ".local" in names
    assert ".ssh" not in names and "id_rsa" not in names and "server.pem" not in names


def test_list_entries_have_type_and_size(policy):
    entries = {e["name"]: e for e in filesystem._list(policy, "~/notes")["entries"]}
    assert entries["todo.txt"] == {"name": "todo.txt", "type": "file", "size": 24}


def test_list_rejects_file(policy):
    with pytest.raises(FilesystemError, match="not a directory"):
        filesystem._list(policy, "~/notes/todo.txt")


# --- read -----------------------------------------------------------------


def test_read_whole_file(policy):
    result = filesystem._read(policy, "~/notes/todo.txt")
    assert result["content"] == "buy milk\nfix nova\nsleep\n"
    assert (result["start_line"], result["end_line"]) == (1, 3)
    assert result["truncated"] is False
    assert "next_start_line" not in result
    assert result["path"] == "~/notes/todo.txt"


def test_read_window(policy, monkeypatch):
    monkeypatch.setattr(filesystem, "MIN_READ_LINES", 1)
    result = filesystem._read(policy, "~/notes/todo.txt", start_line=2, max_lines=1)
    assert result["content"] == "fix nova\n"
    assert (result["start_line"], result["end_line"]) == (2, 2)
    assert result["truncated"] is True
    assert result["next_start_line"] == 3


def test_read_has_minimum_window(policy, home):
    (home / "long.txt").write_text("".join(f"{i}\n" for i in range(1, 101)))
    result = filesystem._read(policy, "~/long.txt", max_lines=2)
    assert (result["end_line"], result["next_start_line"]) == (50, 51)


def test_read_find_jumps_to_the_passage(policy, home):
    lines = [f"line {i}\n" for i in range(1, 301)]
    lines[199] = "## Next recommended task\n"
    (home / "state.md").write_text("".join(lines))
    result = filesystem._read(policy, "~/state.md", find="next RECOMMENDED")
    assert result["found_line"] == 200
    assert result["start_line"] == 197
    assert result["content"].splitlines()[3] == "## Next recommended task"


def test_read_find_missing(policy):
    with pytest.raises(FilesystemError, match="not found"):
        filesystem._read(policy, "~/notes/todo.txt", find="nowhere")


def test_read_caps_bytes(policy, home, monkeypatch):
    monkeypatch.setattr(filesystem, "MAX_READ_BYTES", 20)
    (home / "big.txt").write_text("0123456789\n" * 10)
    result = filesystem._read(policy, "~/big.txt")
    assert result["content"] == "0123456789\n"
    assert result["truncated"] is True


def test_read_refuses_binary(policy, home):
    (home / "blob.bin").write_bytes(b"\x7fELF\0\0\0")
    with pytest.raises(FilesystemError, match="binary"):
        filesystem._read(policy, "~/blob.bin")


def test_read_refuses_directory(policy):
    with pytest.raises(FilesystemError, match="not a regular file"):
        filesystem._read(policy, "~/notes")


# --- search ---------------------------------------------------------------


def test_search_by_name(policy):
    result = filesystem._search(policy, "~", name="*.txt")
    assert [r["path"] for r in result["results"]] == ["~/notes/todo.txt"]


def test_search_by_content_skips_node_modules_and_secrets(policy):
    result = filesystem._search(policy, "~", contains="FIX NOVA", show_hidden=True)
    hits = {r["path"]: r["line"] for r in result["results"]}
    assert hits == {"~/notes/todo.txt": 2, "~/project/main.py": 1}


def test_search_limits_results(policy):
    result = filesystem._search(policy, "~", max_results=1)
    assert len(result["results"]) == 1
    assert result["truncated"] is True


def test_search_root_must_be_allowed(policy):
    with pytest.raises(FilesystemError, match="blocked"):
        filesystem._search(policy, "~/.ssh")


# --- registry integration -------------------------------------------------


@pytest.mark.asyncio
async def test_tools_run_through_registry_without_prompt(home):
    registry = ToolRegistry()
    registry.register_many(filesystem.get_tools(home))
    for tool in registry.list_tools():
        assert tool.risk == RiskLevel.READ
    ok = await registry.execute("filesystem.read", {"path": "~/notes/ideas.md"})
    assert ok.success and ok.data["content"].startswith("# Ideas")
    denied = await registry.execute("filesystem.read", {"path": "~/.ssh/id_ed25519"})
    assert not denied.success and "blocked" in denied.error
