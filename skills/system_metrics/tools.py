"""System Metrics skill — live hardware/OS statistics as a Markdown report."""

import json
import os
import shutil
import subprocess
import time
from datetime import datetime, timedelta

from nova.tools.tool import RiskLevel, Tool

REPORT_DESCRIPTION = (
    "Generates a Markdown report of live system statistics: CPU model, cores, "
    "load and temperature; GPU name, VRAM usage, utilization and temperature; "
    "memory usage; disk usage; NVMe/PCH temperatures and uptime."
)

REPORT_PARAMETERS = {
    "type": "object",
    "properties": {},
}


def _run(cmd: list[str], timeout: int = 3) -> str | None:
    try:
        return subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def _cpu_info() -> dict:
    info: dict = {}
    try:
        with open("/proc/cpuinfo") as f:
            lines = f.read().splitlines()
        model = next(
            (l.split(":", 1)[1].strip() for l in lines if l.startswith("model name")),
            "unknown",
        )
        cores = sum(1 for l in lines if l.startswith("processor"))
        info["model"] = model
        info["cores"] = cores
    except OSError:
        pass
    info["logical_cores"] = os.cpu_count() or 0
    try:
        load = open("/proc/loadavg").read().split()
        info["load"] = [float(v) for v in load[:3]]
    except (OSError, IndexError, ValueError):
        pass
    return info


def _cpu_temperature() -> float | None:
    data = _run(["sensors", "-j"])
    if not data:
        return None
    try:
        parsed = json.loads(data)
    except json.JSONDecodeError:
        return None
    for name, block in parsed.items():
        if not name.startswith("coretemp"):
            continue
        pkg = block.get("Package id 0", {})
        temp = pkg.get("temp1_input")
        if isinstance(temp, (int, float)):
            return float(temp)
        for key, val in block.items():
            if isinstance(val, dict) and "temp1_input" in val:
                t = val["temp1_input"]
                if isinstance(t, (int, float)):
                    return float(t)
    return None


def _gpu_info() -> dict:
    info: dict = {}
    out = _run(
        [
            "nvidia-smi",
            "--query-gpu=name,driver_version,memory.used,memory.total,"
            "utilization.gpu,temperature.gpu",
            "--format=csv,noheader,nounits",
        ]
    )
    if not out:
        return info
    parts = [p.strip() for p in out.split(",")]
    if len(parts) < 6:
        return info
    info["name"] = parts[0]
    info["driver"] = parts[1]
    info["vram_used_mib"] = int(float(parts[2]))
    info["vram_total_mib"] = int(float(parts[3]))
    info["utilization_pct"] = int(float(parts[4]))
    info["temperature_c"] = int(float(parts[5]))
    return info


def _memory_info() -> dict:
    info: dict = {}
    try:
        with open("/proc/meminfo") as f:
            lines = f.read().splitlines()
        kb = {}
        for l in lines:
            key, _, rest = l.partition(":")
            kb[key] = int(rest.strip().split()[0])
        info["total_mib"] = kb["MemTotal"] // 1024
        info["available_mib"] = kb["MemAvailable"] // 1024
        info["used_mib"] = (kb["MemTotal"] - kb["MemAvailable"]) // 1024
    except (OSError, KeyError, ValueError):
        pass
    return info


def _disk_info() -> dict:
    info: dict = {}
    try:
        usage = shutil.disk_usage("/")
        info["total_gib"] = round(usage.total / 2**30, 1)
        info["used_gib"] = round(usage.used / 2**30, 1)
        info["free_gib"] = round(usage.free / 2**30, 1)
    except OSError:
        pass
    return info


def _sensor_temperatures() -> list[dict]:
    temps: list[dict] = []
    data = _run(["sensors", "-j"])
    if not data:
        return temps
    try:
        parsed = json.loads(data)
    except json.JSONDecodeError:
        return temps
    for name, block in parsed.items():
        if name.startswith("coretemp"):
            continue
        for key, val in block.items():
            if not isinstance(val, dict):
                continue
            t = val.get("temp1_input")
            if isinstance(t, (int, float)):
                temps.append(
                    {
                        "source": f"{name} {key}".strip(),
                        "temperature_c": round(float(t), 1),
                    }
                )
                break
    return temps


def _uptime() -> str:
    try:
        with open("/proc/uptime") as f:
            seconds = float(f.read().split()[0])
        return str(timedelta(seconds=int(seconds)))
    except (OSError, IndexError, ValueError):
        return "unknown"


def _fmt_mib(mib: int) -> str:
    if mib >= 1024:
        return f"{mib / 1024:.1f} GiB"
    return f"{mib} MiB"


def _report() -> dict:
    cpu = _cpu_info()
    gpu = _gpu_info()
    mem = _memory_info()
    disk = _disk_info()
    cpu_temp = _cpu_temperature()
    sensors = _sensor_temperatures()

    lines: list[str] = []
    lines.append(f"# System Report — {datetime.now():%Y-%m-%d %H:%M:%S}")
    lines.append("")

    lines.append("## CPU")
    if cpu.get("model"):
        lines.append(f"- **Model:** {cpu['model']}")
    lines.append(f"- **Cores:** {cpu.get('cores', 'unknown')} "
                 f"({cpu.get('logical_cores', 'unknown')} logical)")
    if cpu.get("load"):
        lines.append(f"- **Load (1/5/15 min):** "
                     f"{cpu['load'][0]:.2f} / {cpu['load'][1]:.2f} / {cpu['load'][2]:.2f}")
    if cpu_temp is not None:
        lines.append(f"- **Temperature:** {cpu_temp:.1f} °C")
    lines.append("")

    lines.append("## GPU")
    if gpu:
        lines.append(f"- **Model:** {gpu['name']} (driver {gpu['driver']})")
        lines.append(f"- **VRAM:** {_fmt_mib(gpu['vram_used_mib'])} used / "
                     f"{_fmt_mib(gpu['vram_total_mib'])} total "
                     f"({gpu['vram_used_mib'] * 100 // gpu['vram_total_mib']}%)")
        lines.append(f"- **Utilization:** {gpu['utilization_pct']}%")
        lines.append(f"- **Temperature:** {gpu['temperature_c']} °C")
    else:
        lines.append("- No NVIDIA GPU detected via nvidia-smi")
    lines.append("")

    lines.append("## Memory")
    if mem:
        pct = mem["used_mib"] * 100 // mem["total_mib"]
        lines.append(f"- **Total:** {_fmt_mib(mem['total_mib'])}")
        lines.append(f"- **Used:** {_fmt_mib(mem['used_mib'])} ({pct}%)")
        lines.append(f"- **Available:** {_fmt_mib(mem['available_mib'])}")
    lines.append("")

    lines.append("## Disk (/)")
    if disk:
        pct = disk["used_gib"] * 100 // disk["total_gib"]
        lines.append(f"- **Total:** {disk['total_gib']} GiB")
        lines.append(f"- **Used:** {disk['used_gib']} GiB ({pct}%)")
        lines.append(f"- **Free:** {disk['free_gib']} GiB")
    lines.append("")

    if sensors:
        lines.append("## Temperatures")
        for s in sensors:
            lines.append(f"- **{s['source']}:** {s['temperature_c']} °C")
        lines.append("")

    lines.append(f"## Uptime\n\n{_uptime()}")

    return {"report": "\n".join(lines)}


def get_tools() -> list[Tool]:
    return [
        Tool(
            name="system_metrics.report",
            description=REPORT_DESCRIPTION,
            parameters=REPORT_PARAMETERS,
            risk=RiskLevel.READ,
            permission="system_metrics.report",
            implementation=_report,
        )
    ]