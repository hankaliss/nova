"""Filesystem READ tools for Nova: list, read and search files.

READ tools run without a permission prompt, so the path policy is the only
guard (AD-030): paths must resolve (symlinks followed) inside the user's
home directory, and secret stores (SSH/GPG keys, keyrings, browser
profiles, credential files, Nova's own data) are refused outright.
"""

import fnmatch
import os
import re
from pathlib import Path
from typing import Any

from nova.tools.tool import RiskLevel, Tool

MAX_READ_BYTES = 100_000
DEFAULT_MAX_LINES = 400
# Small models ask for 1-3 lines and then give up; never return less.
MIN_READ_LINES = 50
MAX_LIST_ENTRIES = 500
MAX_SEARCH_RESULTS = 200
MAX_SEARCH_FILES = 5_000
MAX_CONTENT_SCAN_BYTES = 1_000_000
# Large generated trees nobody wants searched.
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", ".cache"}

# Relative to home. These paths, and anything inside them, are refused.
DENIED_PATHS = (
    ".ssh",
    ".gnupg",
    ".password-store",
    ".local/share/keyrings",
    ".local/share/kwalletd",
    ".local/share/nova",
    ".config/nova/mcp.json",
    ".config/nova/nova.db",
    ".mozilla",
    ".config/google-chrome",
    ".config/chromium",
    ".config/BraveSoftware",
    ".config/gh",
    ".aws",
    ".kube",
    ".docker",
)
# File names refused anywhere under home.
DENIED_NAMES = (
    "*.pem",
    "*.key",
    ".env",
    ".env.*",
    ".netrc",
    ".git-credentials",
    ".pgpass",
    ".pypirc",
    ".npmrc",
)
_SSH_KEY = re.compile(r"^id_(rsa|dsa|ecdsa|ed25519)")


class FilesystemError(Exception):
    """Raised when a path is outside the policy or cannot be read."""


class PathPolicy:
    """Decides which paths the filesystem tools may touch."""

    def __init__(self, home: Path | None = None) -> None:
        self.home = (home or Path.home()).resolve()
        self._denied = [self.home / d for d in DENIED_PATHS]

    def is_denied(self, resolved: Path) -> bool:
        """True for a resolved path that is a secret or inside a secret store."""
        if any(resolved == d or d in resolved.parents for d in self._denied):
            return True
        name = resolved.name
        if _SSH_KEY.match(name):
            return True
        return any(fnmatch.fnmatch(name, p) for p in DENIED_NAMES)

    def check(self, path: str) -> Path:
        """Resolve ``path`` (``~`` and relative to home) and enforce the policy."""
        if not path or not path.strip():
            raise FilesystemError("path is required")
        text = path.strip()
        if text == "~" or text.startswith("~/"):
            text = str(self.home) + text[1:]
        raw = Path(text)
        if not raw.is_absolute():
            raw = self.home / raw
        resolved = raw.resolve()
        if resolved != self.home and self.home not in resolved.parents:
            raise FilesystemError(
                f"{path}: outside the home directory; only paths under "
                f"{self.home} can be read"
            )
        if self.is_denied(resolved):
            raise FilesystemError(f"{path}: blocked (private keys, credentials or secrets)")
        return resolved

    def display(self, resolved: Path) -> str:
        """Show a path as ``~/...`` to keep results short."""
        if resolved == self.home:
            return "~"
        return "~/" + str(resolved.relative_to(self.home))


def _is_hidden(name: str) -> bool:
    return name.startswith(".")


def _list(policy: PathPolicy, path: str = "~", show_hidden: bool = False) -> dict[str, Any]:
    target = policy.check(path)
    if not target.is_dir():
        raise FilesystemError(f"{path}: not a directory")
    entries = []
    truncated = False
    try:
        children = sorted(target.iterdir(), key=lambda p: p.name.lower())
    except OSError as e:
        raise FilesystemError(f"{path}: {e.strerror or e}") from e
    for child in children:
        if not show_hidden and _is_hidden(child.name):
            continue
        try:
            resolved = child.resolve()
        except OSError:
            continue
        if policy.is_denied(resolved) or policy.is_denied(child):
            continue
        if len(entries) >= MAX_LIST_ENTRIES:
            truncated = True
            break
        try:
            is_dir = child.is_dir()
            size = None if is_dir else child.stat().st_size
        except OSError:
            continue
        entry: dict[str, Any] = {"name": child.name, "type": "dir" if is_dir else "file"}
        if size is not None:
            entry["size"] = size
        entries.append(entry)
    return {"path": policy.display(target), "entries": entries, "truncated": truncated}


# Lines shown above a ``find`` match, so a heading's context is included.
FIND_CONTEXT_LINES = 3


def _read(
    policy: PathPolicy,
    path: str,
    start_line: int = 1,
    max_lines: int = DEFAULT_MAX_LINES,
    find: str = "",
) -> dict[str, Any]:
    target = policy.check(path)
    if not target.is_file():
        raise FilesystemError(f"{path}: not a regular file")
    found_line = None
    if find:
        # Small models read a long file's first window and answered "not
        # there"; find jumps straight to the passage.
        found_line = _contains(target, find)
        if found_line is None:
            raise FilesystemError(f"{path}: {find!r} not found")
        start_line = max(1, found_line - FIND_CONTEXT_LINES)
    start_line = max(1, int(start_line))
    max_lines = max(MIN_READ_LINES, min(int(max_lines), 2_000))
    try:
        size = target.stat().st_size
        with open(target, "rb") as f:
            head = f.read(8_192)
            if b"\0" in head:
                raise FilesystemError(f"{path}: binary file, not shown")
            f.seek(0)
            lines: list[str] = []
            used = 0
            line_no = 0
            truncated = False
            for raw in f:
                line_no += 1
                if line_no < start_line:
                    continue
                if len(lines) >= max_lines or used + len(raw) > MAX_READ_BYTES:
                    truncated = True
                    break
                lines.append(raw.decode("utf-8", errors="replace"))
                used += len(raw)
    except OSError as e:
        raise FilesystemError(f"{path}: {e.strerror or e}") from e
    end_line = start_line + len(lines) - 1 if lines else start_line - 1
    result: dict[str, Any] = {
        "path": policy.display(target),
        "size": size,
        "start_line": start_line,
        "end_line": end_line,
        "truncated": truncated,
        "content": "".join(lines),
    }
    if truncated:
        result["next_start_line"] = end_line + 1
    if found_line is not None:
        result["found_line"] = found_line
    return result


def _contains(path: Path, needle: str) -> int | None:
    """Return the first matching line number of ``needle`` (case-insensitive)."""
    try:
        if path.stat().st_size > MAX_CONTENT_SCAN_BYTES:
            return None
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return None
    if b"\0" in data[:8_192]:
        return None
    lowered = needle.lower()
    for no, line in enumerate(data.decode("utf-8", errors="replace").splitlines(), 1):
        if lowered in line.lower():
            return no
    return None


def _search(
    policy: PathPolicy,
    path: str = "~",
    name: str = "*",
    contains: str = "",
    max_results: int = 50,
    show_hidden: bool = False,
) -> dict[str, Any]:
    root = policy.check(path)
    if not root.is_dir():
        raise FilesystemError(f"{path}: not a directory")
    max_results = max(1, min(int(max_results), MAX_SEARCH_RESULTS))
    pattern = name or "*"
    results: list[dict[str, Any]] = []
    scanned = 0
    truncated = False
    for dirpath, dirnames, filenames in os.walk(root):
        here = Path(dirpath)
        dirnames[:] = sorted(
            d for d in dirnames
            if d not in SKIP_DIRS
            and (show_hidden or not _is_hidden(d))
            and not policy.is_denied(here / d)
        )
        for fname in sorted(filenames):
            if not show_hidden and _is_hidden(fname):
                continue
            if not fnmatch.fnmatch(fname.lower(), pattern.lower()):
                continue
            fpath = here / fname
            if fpath.is_symlink() or policy.is_denied(fpath):
                continue
            scanned += 1
            if scanned > MAX_SEARCH_FILES:
                truncated = True
                break
            hit: dict[str, Any] = {"path": policy.display(fpath)}
            if contains:
                line = _contains(fpath, contains)
                if line is None:
                    continue
                hit["line"] = line
            results.append(hit)
            if len(results) >= max_results:
                truncated = True
                break
        if truncated:
            break
    return {
        "root": policy.display(root),
        "name": pattern,
        "contains": contains,
        "results": results,
        "truncated": truncated,
    }


def get_tools(home: Path | None = None) -> list[Tool]:
    policy = PathPolicy(home)
    return [
        Tool(
            name="filesystem.list",
            description=(
                "List a directory under the user's home: names, types, sizes. "
                "Paths may use ~. Hidden entries only with show_hidden."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Directory, default ~"},
                    "show_hidden": {"type": "boolean", "description": "Include dotfiles"},
                },
            },
            risk=RiskLevel.READ,
            permission="filesystem.read",
            implementation=lambda path="~", show_hidden=False: _list(
                policy, path, show_hidden
            ),
        ),
        Tool(
            name="filesystem.read",
            description=(
                "Read a text file under the user's home. Returns up to max_lines "
                "lines from start_line (1-based). To read a specific section or "
                "passage, pass find (text on that line, case-insensitive) and the "
                "read starts there. If truncated is true, there is more: call "
                "again with start_line=next_start_line."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path, e.g. ~/notes.txt"},
                    "start_line": {"type": "integer", "description": "First line, default 1"},
                    "max_lines": {"type": "integer", "description": "Default 400, min 50, max 2000"},
                    "find": {
                        "type": "string",
                        "description": "Start at the first line containing this text, e.g. a heading",
                    },
                },
                "required": ["path"],
            },
            risk=RiskLevel.READ,
            permission="filesystem.read",
            implementation=lambda path, start_line=1, max_lines=DEFAULT_MAX_LINES, find="": _read(
                policy, path, start_line, max_lines, find
            ),
        ),
        Tool(
            name="filesystem.search",
            description=(
                "Find files under a directory in the user's home by name glob "
                "(e.g. '*.py') and optionally by text they contain "
                "(case-insensitive). Skips .git, node_modules and hidden folders."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Directory to search, default ~"},
                    "name": {"type": "string", "description": "File name glob, default *"},
                    "contains": {"type": "string", "description": "Text the file must contain"},
                    "max_results": {"type": "integer", "description": "Default 50, max 200"},
                    "show_hidden": {"type": "boolean", "description": "Include dotfiles"},
                },
            },
            risk=RiskLevel.READ,
            permission="filesystem.read",
            implementation=lambda path="~", name="*", contains="", max_results=50,
            show_hidden=False: _search(
                policy, path, name, contains, max_results, show_hidden
            ),
        ),
    ]
