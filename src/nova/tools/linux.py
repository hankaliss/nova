"""Linux system tools for Nova.

Tools here use only the standard library. Structured subprocess calls for
read-only tools. Shell execution lives in nova.tools.shell (a persistent
shared session), not here.
"""

import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Any

from nova.tools.tool import RiskLevel, Tool


def _cpu_info() -> dict[str, Any]:
    try:
        with open("/proc/cpuinfo") as f:
            lines = f.readlines()
        model = ""
        for line in lines:
            if line.startswith("model name"):
                model = line.split(":", 1)[1].strip()
                break
    except OSError:
        model = platform.processor() or "unknown"
    return {
        "model": model,
        "cores": os.cpu_count() or 0,
        "arch": platform.machine(),
    }


def _memory_info() -> dict[str, Any]:
    try:
        with open("/proc/meminfo") as f:
            data = {}
            for line in f:
                key, value = line.split(":", 1)
                data[key.strip()] = int(value.strip().split()[0])
        total = data.get("MemTotal", 0) * 1024
        available = data.get("MemAvailable", 0) * 1024
        return {
            "total_bytes": total,
            "available_bytes": available,
            "used_bytes": total - available,
            "total_gb": round(total / 1e9, 1),
            "available_gb": round(available / 1e9, 1),
        }
    except (OSError, ValueError):
        return {"error": "unable to read /proc/meminfo"}


def _gpu_info() -> dict[str, Any]:
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,memory.used,driver_version,"
                "temperature.gpu,utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return {"error": "nvidia-smi failed"}
        parts = [p.strip() for p in result.stdout.strip().split(",")]
        return {
            "name": parts[0],
            "vram_total_mib": int(parts[1]),
            "vram_used_mib": int(parts[2]),
            "driver": parts[3],
            "temperature_c": int(parts[4]),
            "utilization_pct": int(parts[5]),
        }
    except (subprocess.SubprocessError, FileNotFoundError, ValueError, IndexError):
        return {"error": "unable to query NVIDIA GPU"}


def _disk_usage() -> dict[str, Any]:
    usage = shutil.disk_usage("/")
    return {
        "total_bytes": usage.total,
        "used_bytes": usage.used,
        "free_bytes": usage.free,
        "total_gb": round(usage.total / 1e9, 1),
        "used_gb": round(usage.used / 1e9, 1),
        "free_gb": round(usage.free / 1e9, 1),
    }


def _uptime() -> dict[str, Any]:
    try:
        with open("/proc/uptime") as f:
            seconds = float(f.read().split()[0])
        return {"seconds": int(seconds)}
    except (OSError, ValueError):
        return {"error": "unable to read /proc/uptime"}


_PS_SORT = {"cpu": "-pcpu", "memory": "-rss"}
MAX_PROCESSES = 50


def _processes(sort: str = "cpu", limit: int = 15) -> dict[str, Any]:
    if sort not in _PS_SORT:
        raise ValueError("sort must be 'cpu' or 'memory'")
    limit = max(1, min(int(limit), MAX_PROCESSES))
    try:
        result = subprocess.run(
            ["ps", "-eo", "pid=,user=,pcpu=,pmem=,rss=,comm=", f"--sort={_PS_SORT[sort]}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (subprocess.SubprocessError, FileNotFoundError):
        return {"error": "unable to run ps"}
    processes = []
    for line in result.stdout.splitlines()[:limit]:
        parts = line.split(None, 5)
        if len(parts) < 6:
            continue
        pid, user, cpu, mem, rss, name = parts
        processes.append({
            "pid": int(pid),
            "user": user,
            "cpu_pct": float(cpu),
            "mem_pct": float(mem),
            "rss_mb": round(int(rss) / 1024, 1),
            "name": name,
        })
    return {"sort": sort, "processes": processes}


HWMON = Path("/sys/class/hwmon")


def _temperatures() -> dict[str, Any]:
    """All kernel hwmon temperature sensors (CPU cores, NVMe, chipset, ...)."""
    sensors = []
    for dev in sorted(HWMON.glob("hwmon*")):
        try:
            chip = (dev / "name").read_text().strip()
        except OSError:
            continue
        for temp in sorted(dev.glob("temp*_input")):
            try:
                celsius = int(temp.read_text().strip()) / 1000
            except (OSError, ValueError):
                continue
            label_file = dev / temp.name.replace("_input", "_label")
            try:
                label = label_file.read_text().strip()
            except OSError:
                label = temp.name.removesuffix("_input")
            sensors.append({"chip": chip, "label": label, "celsius": round(celsius, 1)})
    if not sensors:
        return {"error": "no temperature sensors found"}
    return {"sensors": sensors}


_TOOLS: list[Tool] = [
    Tool(
        name="linux.cpu_info",
        description="Get CPU information: model, core count, architecture.",
        parameters={
            "type": "object",
            "properties": {},
        },
        risk=RiskLevel.READ,
        permission="system.info",
        implementation=_cpu_info,
    ),
    Tool(
        name="linux.memory_info",
        description="Get system memory usage: total, available, used.",
        parameters={
            "type": "object",
            "properties": {},
        },
        risk=RiskLevel.READ,
        permission="system.info",
        implementation=_memory_info,
    ),
    Tool(
        name="linux.gpu_info",
        description="Get NVIDIA GPU information: model, VRAM usage, driver version.",
        parameters={
            "type": "object",
            "properties": {},
        },
        risk=RiskLevel.READ,
        permission="system.info",
        implementation=_gpu_info,
    ),
    Tool(
        name="linux.disk_usage",
        description="Get disk usage for the root filesystem.",
        parameters={
            "type": "object",
            "properties": {},
        },
        risk=RiskLevel.READ,
        permission="system.info",
        implementation=_disk_usage,
    ),
    Tool(
        name="linux.uptime",
        description="Get system uptime in seconds.",
        parameters={
            "type": "object",
            "properties": {},
        },
        risk=RiskLevel.READ,
        permission="system.info",
        implementation=_uptime,
    ),
    Tool(
        name="linux.processes",
        description=(
            "List the top processes by CPU or memory use: pid, user, CPU %, "
            "memory %, resident MB, name."
        ),
        parameters={
            "type": "object",
            "properties": {
                "sort": {"type": "string", "enum": ["cpu", "memory"], "description": "Default cpu"},
                "limit": {"type": "integer", "description": "Default 15, max 50"},
            },
        },
        risk=RiskLevel.READ,
        permission="system.info",
        implementation=_processes,
    ),
    Tool(
        name="linux.temperatures",
        description=(
            "Read all temperature sensors (CPU package and cores, NVMe, chipset) "
            "in °C. GPU temperature is in linux.gpu_info."
        ),
        parameters={
            "type": "object",
            "properties": {},
        },
        risk=RiskLevel.READ,
        permission="system.info",
        implementation=_temperatures,
    ),
]


def get_tools() -> list[Tool]:
    return _TOOLS