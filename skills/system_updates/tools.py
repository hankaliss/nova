import subprocess

from nova.tools.tool import RiskLevel, Tool


def _check() -> dict:
    """List available package updates via `pacman -Qu`."""
    result = subprocess.run(
        ["pacman", "-Qu"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        error = result.stderr.strip() or "pacman -Qu failed"
        return {"error": error}
    updates = [line for line in result.stdout.splitlines() if line.strip()]
    return {"count": len(updates), "updates": updates}


def get_tools():
    return [
        Tool(
            name="system_updates.check",
            description=(
                "List available package updates from pacman (read-only; does "
                "not refresh the sync databases or install anything)."
            ),
            parameters={},
            risk=RiskLevel.READ,
            implementation=_check,
        )
    ]