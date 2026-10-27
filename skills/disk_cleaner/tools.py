"""Disk space cleaner: where did the space go, as a report (issue #14).

Scanning is READ only. Clearing is limited to one folder directly under
~/.cache and always asks (WRITE, never auto-approved). Package cleanup needs
root, so it is only suggested.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

from nova.tools.report import Bar, Finding, Report, Stat, plural, short_path, with_artifact
from nova.tools.tool import RiskLevel, Tool

TOP = 10
SCAN_DEPTH = 4  # node_modules search depth below the projects folder
MAX_NODE_MODULES = 30
_SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", ".cache", ".local", ".cargo", ".rustup"}
_PACCACHE = re.compile(r"\(disk space saved: ([\d.]+ \w+)\)")
_OLLAMA_DIRS = (Path.home() / ".ollama" / "models", Path("/usr/share/ollama/.ollama/models"))


def _fmt(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _du(path: Path, depth: int = 0, timeout: int = 60) -> dict[str, int]:
    """Sizes in bytes of `path` and its subfolders down to `depth` (stays on one filesystem)."""
    try:
        out = subprocess.run(
            ["du", "-x", "-B1", f"--max-depth={depth}", str(path)],
            capture_output=True, text=True, timeout=timeout,
        ).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        out = exc.stdout if isinstance(getattr(exc, "stdout", None), str) else ""
    sizes = {}
    for line in out.splitlines():
        size, _, name = line.partition("\t")
        if size.isdigit():
            sizes[name] = int(size)
    return sizes


def _children(path: Path, depth: int = 1, timeout: int = 60) -> list[tuple[str, int]]:
    sizes = _du(path, depth, timeout)
    sizes.pop(str(path), None)
    return sorted(sizes.items(), key=lambda kv: -kv[1])


def _node_modules(root: Path) -> list[tuple[str, int]]:
    found = []
    base = len(root.parts)
    for current, dirs, _ in os.walk(root):
        here = Path(current)
        if len(here.parts) - base >= SCAN_DEPTH:
            dirs[:] = []
        if "node_modules" in dirs:
            found.append(str(here / "node_modules"))
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS and d != "node_modules"]
        if len(found) >= MAX_NODE_MODULES:
            break
    sizes = [(p, _du(Path(p)).get(p, 0)) for p in found]
    return sorted(sizes, key=lambda kv: -kv[1])


def _pacman_cache() -> tuple[int, str]:
    """Bytes in the pacman cache and what `paccache -d` says old versions would free."""
    cache = Path("/var/cache/pacman/pkg")
    total = _du(cache).get(str(cache), 0) if cache.is_dir() else 0
    reclaim = ""
    if shutil.which("paccache"):
        try:
            out = subprocess.run(["paccache", "-d"], capture_output=True, text=True, timeout=30).stdout
            m = _PACCACHE.search(out)
            reclaim = m.group(1) if m else ""
        except (OSError, subprocess.SubprocessError):
            pass
    return total, reclaim


def _scan(path: str = "", projects: str = "") -> dict:
    home = Path.home()
    root = Path(path).expanduser() if path else home
    if not root.is_dir():
        raise ValueError(f"not a folder: {root}")
    folders = _children(root)[:TOP]
    total = _du(root).get(str(root), 0)
    caches = _children(home / ".cache")[:TOP]
    cache_total = sum(s for _, s in _children(home / ".cache"))
    ollama = next(((d, _du(d).get(str(d), 0)) for d in _OLLAMA_DIRS if d.is_dir()), None)
    modules = _node_modules(Path(projects).expanduser() if projects else home / "Projects")
    pacman, reclaim = _pacman_cache()
    usage = shutil.disk_usage(root)

    summary = {
        "folder": str(root),
        "size": _fmt(total),
        "disk_free": _fmt(usage.free),
        "disk_total": _fmt(usage.total),
        "largest_folders": [f"{p}: {_fmt(s)}" for p, s in folders],
        "cache_total": _fmt(cache_total),
        "largest_caches": [f"{Path(p).name}: {_fmt(s)}" for p, s in caches[:5]],
        "ollama_models": f"{ollama[0]}: {_fmt(ollama[1])}" if ollama else "none found",
        "node_modules": {"count": len(modules), "total": _fmt(sum(s for _, s in modules)),
                         "largest": [f"{p}: {_fmt(s)}" for p, s in modules[:3]]},
        "pacman_cache": _fmt(pacman),
        "old_package_versions_reclaimable": reclaim or "unknown",
    }
    return with_artifact(summary, _report(root, total, usage, folders, caches, cache_total, ollama,
                                          modules, pacman, reclaim))


def _report(root, total, usage, folders, caches, cache_total, ollama, modules, pacman, reclaim) -> Report:
    report = Report("Disk space", subtitle=f"Where the space in {short_path(str(root))} went",
                    kicker="Nova · disk cleaner")
    used_pct = 100 * (usage.total - usage.free) / usage.total if usage.total else 0
    report.stats([
        Stat("Free", _fmt(usage.free), "danger" if usage.free / max(usage.total, 1) < 0.1 else "ok",
             f"of {_fmt(usage.total)} ({used_pct:.0f}% used)"),
        Stat("This folder", _fmt(total)),
        Stat("Caches", _fmt(cache_total), hint="~/.cache"),
        Stat("Package cache", _fmt(pacman), hint=f"{reclaim} in old versions" if reclaim else ""),
    ])
    if folders:
        report.section("Largest folders", note=short_path(str(root)))
        report.bars([Bar(Path(p).name or p, s, folders[0][1], _fmt(s)) for p, s in folders])
    if caches:
        report.section("Largest caches", note="safe to clear; apps rebuild them")
        report.bars([Bar(Path(p).name, s, caches[0][1], _fmt(s), "warn") for p, s in caches])
    findings = []
    if ollama and ollama[1]:
        findings.append(Finding("Ollama models", f"{short_path(str(ollama[0]))}: remove unused ones with `ollama rm <model>`",
                                tone="muted", tag=_fmt(ollama[1])))
    if modules:
        findings.append(Finding(f"{plural(len(modules), 'node_modules folder')}",
                                "; ".join(f"{short_path(p, 50)} {_fmt(s)}" for p, s in modules[:3]),
                                tone="muted", tag=_fmt(sum(s for _, s in modules))))
    if reclaim:
        findings.append(Finding("Old package versions", "Run `sudo paccache -r` yourself (needs root)",
                                tone="warn", tag=reclaim))
    if findings:
        report.section("Other places")
        report.findings(findings)
    return report


def _clear_cache(name: str) -> dict:
    cache = Path.home() / ".cache"
    if not name or "/" in name or name in (".", ".."):
        raise ValueError("name must be a single folder directly under ~/.cache")
    target = cache / name
    if target.is_symlink() or not target.is_dir():
        raise ValueError(f"no cache folder named {name!r} in ~/.cache")
    before = _du(target).get(str(target), 0)
    failed = 0
    for entry in target.iterdir():
        try:
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry)
            else:
                entry.unlink()
        except OSError:
            failed += 1
    after = _du(target).get(str(target), 0)
    return {"cleared": str(target), "freed": _fmt(max(before - after, 0)), "entries_failed": failed}


def get_tools():
    return [
        Tool(
            name="disk_cleaner.scan",
            description=(
                "Find where disk space went: largest folders (default: home), ~/.cache, "
                "Ollama models, node_modules under ~/Projects and old pacman package "
                "versions. Read only. Shows a report to the user by itself; returns a "
                "short summary for you to explain."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Folder to break down (default: home)"},
                    "projects": {"type": "string", "description": "Where to look for node_modules (default: ~/Projects)"},
                },
            },
            risk=RiskLevel.READ,
            implementation=_scan,
        ),
        Tool(
            name="disk_cleaner.clear_cache",
            description=(
                "Delete the contents of one cache folder directly under ~/.cache (e.g. "
                "name='mesa_shader_cache'). Asks the user every time."
            ),
            parameters={
                "type": "object",
                "properties": {"name": {"type": "string", "description": "Folder name inside ~/.cache"}},
                "required": ["name"],
            },
            risk=RiskLevel.WRITE,
            auto_approvable=False,
            implementation=_clear_cache,
        ),
    ]
