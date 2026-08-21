"""Linux system tools for Nova.

Tools here use only the standard library. Structured subprocess calls for
read-only tools; shell execution for the explicit shell.execute tool.
"""

import os
import platform
import shutil
import subprocess
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


def _shell_execute(command: str) -> dict[str, Any]:
    """Run a shell command and return stdout/stderr."""
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"error": "command timed out after 30 seconds"}
    except Exception as e:
        return {"error": str(e)}


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
        name="shell.execute",
        description="Execute a shell command and return its output. Use this when you need to run system commands like systemctl, ps, df, etc.",
        parameters={
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to execute",
                },
            },
            "required": ["command"],
        },
        risk=RiskLevel.WRITE,
        permission="shell.execute",
        implementation=_shell_execute,
    ),
]


def get_tools() -> list[Tool]:
    return _TOOLS