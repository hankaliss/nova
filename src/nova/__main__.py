"""Nova application entry point.

`nova` launches the Electron frontend (AD-011), which spawns the Python
sidecar (`python -m nova.rpc`). `nova --toggle` shows or hides a running
Nova (or starts it); bind it to a key in KDE's Shortcuts settings (AD-034).
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

ELECTRON_DIR_NAME = "electron"
# Installed package (packaging/arch): the Electron app files and the system
# Electron it was verified with.
INSTALLED_APP_DIR = Path("/usr/lib/nova/app")
SYSTEM_ELECTRON = "electron44"


# Options passed through to Electron's main process (main.cjs).
PASS_THROUGH = ("--toggle",)


def _nova_args(argv: list[str]) -> list[str]:
    return [a for a in argv if a in PASS_THROUGH]


def _electron_command(
    binary: Path, app_dir: Path, extra: list[str] | None = None
) -> list[str]:
    """Electron's command line.

    Nova runs through XWayland: native Wayland can't keep a window on top
    (always_on_top). The platform must come from the command line; set from
    main.cjs it is half-applied in Electron 44 (GPU process crash, window
    never shown). ``NOVA_OZONE_PLATFORM=wayland`` opts into native Wayland.
    """
    platform = os.environ.get("NOVA_OZONE_PLATFORM", "x11")
    return [str(binary), str(app_dir), f"--ozone-platform={platform}", *(extra or [])]


def _run(cmd: list[str], cwd: Path, env: dict[str, str]) -> int:
    print(f"Nova: launching {cmd[0]}", file=sys.stderr)
    try:
        proc = subprocess.Popen(cmd, cwd=cwd, env=env)
        return proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        return proc.wait()


def _installed_app_dir() -> Path:
    return Path(os.environ.get("NOVA_APP_DIR", INSTALLED_APP_DIR))


def _launch_installed() -> int:
    """Run the packaged app with the system Electron."""
    app_dir = _installed_app_dir()
    binary = shutil.which(SYSTEM_ELECTRON)
    if binary is None:
        print(f"Nova: {SYSTEM_ELECTRON} not found; install it with pacman.", file=sys.stderr)
        return 1
    command = _electron_command(Path(binary), app_dir, _nova_args(sys.argv[1:]))
    return _run(command, app_dir, dict(os.environ))


def _launch_electron() -> int:
    """Spawn the Electron frontend and wait for it to exit."""
    root = Path(__file__).resolve().parent.parent.parent
    app_dir = root / ELECTRON_DIR_NAME
    binary = app_dir / "node_modules" / "electron" / "dist" / "electron"

    # Not a source checkout: this is the installed package.
    if not (app_dir / "main.cjs").is_file() and _installed_app_dir().is_dir():
        return _launch_installed()

    if not app_dir.is_dir():
        print(f"Nova: frontend not found at {app_dir}", file=sys.stderr)
        print("Nova: run `npm install` inside the electron/ directory first.", file=sys.stderr)
        return 1
    if not binary.is_file():
        print(
            "Nova: Electron is not installed in electron/ (missing "
            f"{binary}). Run `npm install` inside electron/ first.",
            file=sys.stderr,
        )
        return 1
    extra = _nova_args(sys.argv[1:])
    if not (app_dir / "dist" / "index.html").is_file():
        print("Nova: renderer not built — running `npm run build`…", file=sys.stderr)
        build = subprocess.run(
            ["npm", "run", "build"],
            cwd=app_dir,
            env={**os.environ, "PYTHONPATH": str(root / "src")},
        )
        if build.returncode != 0:
            print("Nova: renderer build failed.", file=sys.stderr)
            return build.returncode
    # A toggle must be quick (it's bound to a key), so it skips the
    # staleness rebuild; the running Nova keeps its renderer anyway.
    elif "--toggle" not in extra:
        # Rebuild when source files are newer than the last build (stale dist
        # silently serves old UI/event code).
        src_dir = app_dir / "src"
        dist_index = app_dir / "dist" / "index.html"
        stale = [
            p
            for p in src_dir.rglob("*")
            if p.is_file() and p.suffix in (".ts", ".tsx", ".css")
        ]
        if any(p.stat().st_mtime > dist_index.stat().st_mtime for p in stale):
            print("Nova: renderer sources changed — rebuilding…", file=sys.stderr)
            build = subprocess.run(
                ["npm", "run", "build"],
                cwd=app_dir,
                env={**os.environ, "PYTHONPATH": str(root / "src")},
            )
            if build.returncode != 0:
                print("Nova: renderer rebuild failed.", file=sys.stderr)
                return build.returncode

    return _run(
        _electron_command(binary, app_dir, extra),
        app_dir,
        {**os.environ, "PYTHONPATH": str(root / "src")},
    )


def main() -> int:
    return _launch_electron()


if __name__ == "__main__":
    sys.exit(main())