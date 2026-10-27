"""System doctor: what went wrong since boot, as a report (issue #12).

One call does all the work in code: failed units, journal errors grouped by
source and deduplicated, kernel warnings, OOM kills, crashes and boot time.
The model gets a compact summary; the user gets a rendered report (AD-039).
"""

import json
import re
import shutil
import subprocess
from collections import Counter, defaultdict
from datetime import datetime

from nova.tools.report import Bar, Finding, Report, Stat, plural, short_path, with_artifact
from nova.tools.tool import RiskLevel, Tool

MAX_ENTRIES = 20_000  # newest journal entries read per query
TOP_SOURCES = 8
SAMPLES_PER_SOURCE = 2
_OOM = re.compile(r"Out of memory: Killed process|oom-kill:|Killed process \d+ \(.*\) total-vm", re.I)
_VARIABLE = re.compile(r"0x[0-9a-f]+|\b[0-9a-f]{8,}\b|\d+", re.I)
# Firewall logs: blocked packets are normal traffic, not problems.
_FIREWALL = re.compile(r"\[UFW (BLOCK|AUDIT|ALLOW|LIMIT)|^nft[_ ]|\bIN=\S* OUT=\S* .*SRC=", re.I)
_SIGNALS = {4: "SIGILL", 6: "SIGABRT", 7: "SIGBUS", 8: "SIGFPE", 11: "SIGSEGV", 5: "SIGTRAP"}


def _run(cmd: list[str], timeout: int = 20) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def _json_lines(text: str) -> list[dict]:
    out = []
    for line in text.splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _message(entry: dict) -> str:
    msg = entry.get("MESSAGE") or ""
    if isinstance(msg, list):  # binary messages arrive as byte lists
        msg = bytes(b for b in msg if isinstance(b, int) and 0 <= b < 256).decode("utf-8", "replace")
    return " ".join(str(msg).split())


def _source(entry: dict) -> str:
    unit = entry.get("_SYSTEMD_USER_UNIT") or entry.get("_SYSTEMD_UNIT") or ""
    ident = entry.get("SYSLOG_IDENTIFIER") or entry.get("_COMM") or ""
    # Template instances (systemd-coredump@0-1-13449_….service) are one source.
    if "@" in unit:
        unit = unit.split("@", 1)[0]
    # app-*.scope units are per-launch; the program name is more useful.
    if unit.startswith("app-") or unit.endswith(".scope") or not unit:
        return ident or unit or "unknown"
    return unit.removesuffix(".service")


def _boot_window(boot: int) -> tuple[str, str] | None:
    """(since, until) of a boot for coredumpctl, from journalctl --list-boots."""
    boots = _run(["journalctl", "--list-boots", "-o", "json", "--no-pager"])
    try:
        entries = json.loads(boots)
    except ValueError:
        return None
    match = [b for b in entries if b.get("index") == boot]
    if not match:
        return None

    def stamp(us: int) -> str:
        return datetime.fromtimestamp(us / 1e6).strftime("%Y-%m-%d %H:%M:%S")

    return stamp(match[0]["first_entry"]), stamp(match[0]["last_entry"])


def _failed_units() -> list[dict]:
    units = []
    for scope, flag in (("system", []), ("user", ["--user"])):
        try:
            data = json.loads(_run(["systemctl", *flag, "--failed", "--output=json", "--no-pager"]) or "[]")
        except ValueError:
            data = []
        for u in data:
            units.append({"unit": u.get("unit", ""), "scope": scope, "description": u.get("description", "")})
    return units


def _group_errors(entries: list[dict]) -> list[dict]:
    by_source: dict[str, Counter] = defaultdict(Counter)
    examples: dict[tuple[str, str], str] = {}
    for e in entries:
        # Counted even without text, so the total matches the sources.
        msg = _message(e) or "(no message)"
        src = _source(e)
        key = _VARIABLE.sub("#", msg)[:160]
        by_source[src][key] += 1
        examples.setdefault((src, key), msg[:300])
    groups = []
    for src, counter in by_source.items():
        samples = [
            {"message": examples[(src, key)], "count": n}
            for key, n in counter.most_common(SAMPLES_PER_SOURCE)
        ]
        groups.append({"source": src, "count": sum(counter.values()),
                       "distinct": len(counter), "samples": samples})
    groups.sort(key=lambda g: g["count"], reverse=True)
    return groups


def _crashes(boot: int) -> list[dict]:
    window = _boot_window(boot)
    if window is None:
        return []
    since, until = window
    try:
        data = json.loads(_run(["coredumpctl", "list", f"--since={since}", f"--until={until}",
                                "--json=short", "--no-pager"]) or "[]")
    except ValueError:
        return []
    # One row per program: a crash loop is one problem, not twelve.
    by_exe: dict[str, dict] = {}
    for c in sorted(data, key=lambda c: c.get("time", 0)):
        exe = str(c.get("exe", ""))
        row = by_exe.setdefault(exe, {
            "program": exe.rsplit("/", 1)[-1] or exe, "path": exe, "count": 0, "signals": Counter(),
        })
        row["count"] += 1
        row["signals"][_SIGNALS.get(c.get("sig"), f"signal {c.get('sig')}")] += 1
        row["time"] = datetime.fromtimestamp(c.get("time", 0) / 1e6).strftime("%H:%M:%S")
    crashes = sorted(by_exe.values(), key=lambda r: r["count"], reverse=True)
    for row in crashes:
        row["signal"] = ", ".join(sig for sig, _ in row.pop("signals").most_common())
    return crashes


def _boot_time() -> str:
    first = _run(["systemd-analyze", "--no-pager"]).splitlines()[:1]
    m = re.search(r"=\s*(?:(\d+)min\s*)?([\d.]+)s\s*$", first[0]) if first else None
    if not m:
        return ""
    minutes, seconds = int(m.group(1) or 0), float(m.group(2))
    return f"{minutes} min {seconds:.0f} s" if minutes else f"{seconds:.1f} s"


def _check(boot: int = 0) -> dict:
    # Without the journal there is nothing to read; "all clear" would be a lie.
    if shutil.which("journalctl") is None:
        raise ValueError("system_doctor needs systemd's journalctl")
    boot = -1 if int(boot) < 0 else 0
    flag = ["-b", str(boot)]
    errors = _json_lines(_run(["journalctl", *flag, "-p", "err", "-o", "json", "-n", str(MAX_ENTRIES),
                               "--no-pager", "-q"], timeout=40))
    kernel_all = _json_lines(_run(["journalctl", *flag, "-k", "-p", "warning", "-o", "json", "-n", "5000",
                                   "--no-pager", "-q"]))
    firewall = sum(1 for e in kernel_all if _FIREWALL.search(_message(e)))
    kernel = [e for e in kernel_all if not _FIREWALL.search(_message(e))]
    failed = _failed_units() if boot == 0 else []
    groups = _group_errors(errors)
    kernel_groups = _group_errors(kernel)
    ooms = [_message(e)[:200] for e in errors + kernel if _OOM.search(_message(e))]
    crashes = _crashes(boot)
    boot_time = _boot_time() if boot == 0 else ""
    label = "this boot" if boot == 0 else "the previous boot"

    summary = {
        "boot": label,
        "failed_units": [f"{u['unit']} ({u['scope']})" for u in failed],
        "errors": len(errors),
        "error_sources": [
            {"source": g["source"], "count": g["count"], "example": g["samples"][0]["message"][:200]}
            for g in groups[:TOP_SOURCES]
        ],
        "kernel_warnings": len(kernel),
        "kernel_examples": [g["samples"][0]["message"][:200] for g in kernel_groups[:3]],
        "firewall_blocked_packets": firewall,
        "oom_kills": len(ooms),
        "crashes": [f"{c['program']} ×{c['count']} ({c['signal']}, last {c['time']})" for c in crashes[:10]],
        "boot_time": boot_time,
    }
    return with_artifact(summary, _report(label, failed, errors, groups, kernel, kernel_groups,
                                          ooms, crashes, boot_time, firewall))


def _report(label, failed, errors, groups, kernel, kernel_groups, ooms, crashes, boot_time,
            firewall) -> Report:
    report = Report("System doctor", subtitle=f"What went wrong during {label}", kicker="Nova · health check")
    stats = [
        Stat("Failed units", len(failed), "danger" if failed else "ok"),
        Stat("Errors", f"{len(errors):,}", "warn" if errors else "ok",
             hint=f"{len(groups)} sources" if groups else "none"),
        Stat("Kernel warnings", f"{len(kernel):,}", "warn" if kernel else "ok"),
        Stat("Crashes", sum(c["count"] for c in crashes), "danger" if crashes else "ok",
             hint=f"{len(crashes)} programs" if crashes else ""),
        Stat("Out of memory", len(ooms), "danger" if ooms else "ok"),
    ]
    if boot_time:
        stats.append(Stat("Boot time", boot_time, "info"))
    report.stats(stats)

    if not (failed or errors or kernel or crashes or ooms):
        report.callout("Nothing went wrong. No failed units, errors or crashes.", tone="ok", title="All clear.")
        return report

    if failed:
        report.section("Failed units", note="currently failed")
        report.table(["Unit", "Scope", "Description"],
                     [[u["unit"], u["scope"], u["description"]] for u in failed],
                     tones=["danger"] * len(failed), mono={0})
    if crashes:
        report.section("Crashes", note="core dumps")
        report.findings([
            Finding(c["program"], detail=f"{c['signal']} · {short_path(c['path'], 60)}", tone="danger",
                    tag=f"×{c['count']}", meta=f"last {c['time']}")
            for c in crashes[:10]
        ])
    if ooms:
        report.section("Out-of-memory kills")
        report.findings([Finding("A process was killed to free memory", detail=m, tone="danger", tag="OOM")
                         for m in ooms[:5]])
    if groups:
        top = groups[:TOP_SOURCES]
        report.section("Errors by source", note=f"{len(errors):,} errors from {len(groups)} sources")
        report.bars([Bar(g["source"], g["count"], top[0]["count"],
                         f"{g['count']:,} · {plural(g['distinct'], 'kind')}", "warn") for g in top])
        report.section("Most frequent errors")
        report.findings([
            Finding(g["source"], detail=s["message"], tone="warn", tag=f"×{s['count']:,}",
                    meta=f"{g['count']:,} errors in total")
            for g in top[:5] for s in g["samples"][:1]
        ])
    if firewall:
        report.chips([(f"Firewall blocked {firewall:,} packets (normal, not counted as warnings)", "muted")])
    if kernel_groups:
        report.section("Kernel warnings", note=f"{len(kernel):,} lines")
        report.findings([
            Finding("kernel", detail=g["samples"][0]["message"], tone="info", tag=f"×{g['samples'][0]['count']:,}")
            for g in kernel_groups[:4]
        ])
    return report


def get_tools():
    return [
        Tool(
            name="system_doctor.check",
            description=(
                "Health check: failed services, journal errors grouped by source, kernel "
                "warnings, out-of-memory kills, crashes and boot time, for this boot "
                "(boot=0) or the previous one (boot=-1). Shows a report to the user by "
                "itself; returns a short summary for you to explain."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "boot": {"type": "integer", "enum": [0, -1], "description": "0 = this boot (default), -1 = previous boot"},
                },
            },
            risk=RiskLevel.READ,
            implementation=_check,
        )
    ]
