"""Packages & updates: what is pending, what changed lately, what could go (issue #15).

READ only. `checkupdates` works on a copy of the sync database, so nothing on
the system is refreshed, installed or removed.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

from nova.tools.report import Finding, Report, Stat, plural, with_artifact
from nova.tools.tool import RiskLevel, Tool

TOP = 10
RECENT = 15
PACMAN_LOG = Path("/var/log/pacman.log")
_LOG_LINE = re.compile(r"^\[([^\]]+)\] \[ALPM\] (installed|upgraded|downgraded|reinstalled) (\S+) \((.+)\)$")
_SIZE_UNITS = {"B": 1, "KiB": 1024, "MiB": 1024**2, "GiB": 1024**3}
# packages whose update deserves a restart or care
_NOTABLE = ("linux", "nvidia", "mesa", "systemd", "glibc", "plasma-workspace", "kwin", "grub")


def _run(cmd: list[str], timeout: int = 60) -> subprocess.CompletedProcess:
    env = {**os.environ, "LC_ALL": "C"}  # sizes and field names must not be localised
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)


def _fmt(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _pending() -> tuple[list[tuple[str, str, str]], str]:
    """(name, old, new) per pending update, and an error text when it could not be checked."""
    if not shutil.which("checkupdates"):
        return [], "checkupdates not found (install pacman-contrib)"
    try:
        res = _run(["checkupdates"], timeout=90)
    except (OSError, subprocess.SubprocessError) as exc:
        return [], f"checkupdates failed: {exc}"
    if res.returncode == 2:  # no updates available
        return [], ""
    if res.returncode != 0:
        return [], res.stderr.strip() or "checkupdates failed"
    updates = []
    for line in res.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[2] == "->":
            updates.append((parts[0], parts[1], parts[3]))
    return updates, ""


def _recent(log: Path | None = None, limit: int = RECENT) -> list[tuple[str, str, str, str]]:
    """(date, action, name, detail) of the latest package changes, newest first."""
    try:
        lines = (log or PACMAN_LOG).read_text(errors="replace").splitlines()
    except OSError:
        return []
    found = []
    for line in reversed(lines):
        m = _LOG_LINE.match(line)
        if m:
            found.append((m.group(1)[:10], m.group(2), m.group(3), m.group(4)))
            if len(found) >= limit:
                break
    return found


def _installed(args: list[str]) -> list[tuple[str, int]]:
    """(name, bytes) for `pacman -Qi`-style output of `args`, largest first."""
    try:
        res = _run(["pacman", *args])
    except (OSError, subprocess.SubprocessError):
        return []
    out, name = [], ""
    for line in res.stdout.splitlines():
        key, _, value = line.partition(":")
        key, value = key.strip(), value.strip()
        if key == "Name":
            name = value
        elif key == "Installed Size" and name:
            num, _, unit = value.partition(" ")
            try:
                out.append((name, int(float(num.replace(",", ".")) * _SIZE_UNITS.get(unit, 1))))
            except ValueError:
                pass
            name = ""
    return sorted(out, key=lambda kv: -kv[1])


def _orphans() -> list[str]:
    try:
        res = _run(["pacman", "-Qdtq"])
    except (OSError, subprocess.SubprocessError):
        return []
    return [n for n in res.stdout.split() if n]


def _report() -> dict:
    if not shutil.which("pacman"):
        raise RuntimeError("pacman not found: this skill is for Arch-based systems")
    pending, error = _pending()
    recent = _recent()
    biggest = _installed(["-Qei"])[:TOP]
    orphans = _orphans()
    orphan_sizes = dict(_installed(["-Qi", *orphans])) if orphans else {}
    orphan_total = sum(orphan_sizes.values())
    notable = [p for p in pending if any(p[0] == n or p[0].startswith(n + "-") for n in _NOTABLE)]

    summary = {
        "pending_count": len(pending),
        "pending": [f"{n} {old} -> {new}" for n, old, new in pending[:30]],
        "needs_attention": [n for n, _, _ in notable],
        "recent_changes": [f"{d} {a} {n} ({v})" for d, a, n, v in recent[:8]],
        "largest_explicit": [f"{n}: {_fmt(s)}" for n, s in biggest[:5]],
        "orphans": orphans[:30],
        "orphans_size": _fmt(orphan_total),
    }
    if error:
        summary["updates_error"] = error
    return with_artifact(summary, _build(pending, error, notable, recent, biggest, orphans, orphan_sizes))


def _build(pending, error, notable, recent, biggest, orphans, orphan_sizes) -> Report:
    report = Report("Packages", subtitle="Updates and installed packages", kicker="Nova · packages")
    report.stats([
        Stat("Pending updates", "?" if error else len(pending),
             "muted" if error else ("warn" if pending else "ok"), error or ("" if pending else "up to date")),
        Stat("Orphans", len(orphans), "warn" if orphans else "ok",
             _fmt(sum(orphan_sizes.values())) if orphans else ""),
        Stat("Recent changes", len(recent), hint="from pacman.log"),
    ])
    if error:
        report.callout(error, tone="warn", title="Updates not checked")
    if pending:
        report.section("Pending updates", note=plural(len(pending), "package"))
        rows = [[n, old, new] for n, old, new in pending]
        tones = ["warn" if (n, old, new) in notable else "" for n, old, new in pending]
        report.table(["Package", "Installed", "New"], rows, tones, mono={1, 2})
        if notable:
            report.callout("Restart or log out afterwards: " + ", ".join(n for n, _, _ in notable),
                           tone="warn", title="Needs attention")
    if recent:
        report.section("Recently changed", note="pacman.log")
        report.table(["Date", "Action", "Package", "Version"], [list(r) for r in recent], mono={3})
    if biggest:
        report.section("Largest explicitly installed", note="what you chose to install")
        report.table(["Package", "Size"], [[n, _fmt(s)] for n, s in biggest])
    if orphans:
        report.section("Orphans", note="installed as dependencies, nothing needs them now")
        report.findings([Finding(n, "", tone="muted", tag=_fmt(orphan_sizes[n]) if n in orphan_sizes else "")
                         for n in orphans[:20]])
    return report


def get_tools():
    return [
        Tool(
            name="packages.report",
            description=(
                "Show pending package updates (checkupdates), recently installed or "
                "updated packages (pacman.log), the largest explicitly installed "
                "packages and orphans. Read only: changes nothing. Shows a report to "
                "the user by itself; returns a short summary for you to explain."
            ),
            parameters={},
            risk=RiskLevel.READ,
            implementation=_report,
        )
    ]
