"""Nova application entry point.

`nova` launches the Electron frontend (AD-011). The QML frontend remains
available via `nova --qml` (or `NOVA_UI=qml nova`).
"""

import asyncio
import os
import subprocess
import sys
from pathlib import Path

ELECTRON_DIR_NAME = "electron"


def _launch_electron() -> int:
    """Spawn the Electron frontend and wait for it to exit."""
    root = Path(__file__).resolve().parent.parent.parent
    app_dir = root / ELECTRON_DIR_NAME
    binary = app_dir / "node_modules" / "electron" / "dist" / "electron"

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
    else:
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

    print(f"Nova: launching {binary}", file=sys.stderr)
    try:
        proc = subprocess.Popen(
            [str(binary), str(app_dir)],
            cwd=app_dir,
            env={**os.environ, "PYTHONPATH": str(root / "src")},
        )
        return proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        return proc.wait()


def main() -> int:
    if "--qml" in sys.argv or os.environ.get("NOVA_UI", "") == "qml":
        return _main_qml()
    return _launch_electron()


def _main_qml() -> int:
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtQuick import QQuickWindow
    from PySide6.QtWidgets import QApplication
    from qasync import QEventLoop

    from nova.agent.agent import Agent
    from nova.kde.tray import TrayIcon
    from nova.llm.ollama import DEFAULT_BASE_URL, OllamaProvider
    from nova.memory.store import MemoryStore
    from nova.security.permissions import PermissionSystem
    from nova.skills.loader import SkillLoader
    from nova.tools.registry import ToolRegistry
    from nova.ui.bridge import ChatBridge

    app = QApplication(sys.argv)
    app.setApplicationName("Nova")
    app.setOrganizationName("Nova")
    app.setQuitOnLastWindowClosed(False)

    # Integrate Qt event loop with python asyncio
    loop = QEventLoop(app)
    asyncio.set_event_loop(loop)

    # Initialize agent backend
    provider = OllamaProvider()
    registry = ToolRegistry()
    registry.register_linux_tools()
    registry.register_web_tools()
    agent = Agent(provider=provider, registry=registry)

    # Memory + permission-aware UI bridge
    store = MemoryStore()

    # Skills: discover + register enabled skills
    loader = None
    skills_dir = Path(__file__).resolve().parent.parent.parent / "skills"
    if skills_dir.is_dir():
        loader = SkillLoader(skills_dir, registry=registry, store=store)
        loaded = loader.register_all(registry)
        if loaded:
            print(f"Nova: loaded {loaded} tools from skills", file=sys.stderr)

    chat_bridge = ChatBridge(agent=agent, store=store, skill_loader=loader)
    registry.permissions = PermissionSystem(
        approval_callback=chat_bridge.request_permission
    )
    chat_bridge.start_conversation()
    chat_bridge.restore_settings()

    # Recreate the provider with the user-configured Ollama URL
    url = chat_bridge.ollamaUrl()
    if url and url != DEFAULT_BASE_URL:
        old = provider
        provider = OllamaProvider(base_url=url)
        agent.provider = provider
        asyncio.ensure_future(old.close())

    engine = QQmlApplicationEngine()

    # Expose chat bridge to QML
    engine.rootContext().setContextProperty("chatBridge", chat_bridge)

    qml_file = Path(__file__).parent / "qml" / "main.qml"
    engine.load(qml_file)

    if not engine.rootObjects():
        print(f"Failed to load QML: {qml_file}", file=sys.stderr)
        return 1

    # Restore the last conversation into the UI and agent context
    chat_bridge.load_history()

    # Dev tools (env-gated; no effect on normal runs):
    #   NOVA_SAMPLE=a|b|c|d — seed demo content for UI screenshots
    #   NOVA_SHOT=/path/out.png — grab the window and save a screenshot
    sample = os.environ.get("NOVA_SAMPLE", "")
    shot_path = os.environ.get("NOVA_SHOT", "")

    def grab_shot() -> None:
        if not (shot_path and window):
            return
        import shiboken6

        qw = shiboken6.wrapInstance(
            int(shiboken6.getCppPointer(window)[0]), QQuickWindow
        )
        img = qw.grabWindow()
        if img.save(shot_path):
            print(f"Nova: screenshot saved to {shot_path}", file=sys.stderr)

    if sample or shot_path:
        from PySide6.QtCore import QTimer

        if sample:
            def seed() -> None:
                if sample == "a":
                    chat_bridge.messageAdded.emit("user", "How is the system doing right now?", False)
                    chat_bridge.messageAdded.emit("assistant", "## CPU\n\n- **Model:** Intel Core i5-9600K\n- **Cores:** 6\n- **Load:** 2.5 / 1.9 / 1.6\n- **Temperature:** 62 \u00B0C\n\n## GPU\n\n- **RTX 2070** \u00B7 6.1 / 8.0 GiB VRAM \u00B7 59 \u00B0C\n\nEverything is healthy.", False)
                elif sample == "b":
                    chat_bridge.messageAdded.emit("user", "Streaming demo \u2014 watch the caret.", False)
                    chat_bridge.messageAdded.emit("assistant", "", True)
                    tokens = ["Here", " is", " a", " streaming", " response", " with", " a", " blinking", " caret"]
                    for i, t in enumerate(tokens):
                        QTimer.singleShot(400 * (i + 1), lambda t=t: chat_bridge.streamUpdated.emit(t))
                elif sample == "c":
                    chat_bridge.messageAdded.emit("user", "Run the system metrics report.", False)
                    chat_bridge.messageAdded.emit("assistant", "", True)
                    QTimer.singleShot(500, lambda: chat_bridge.toolStarted.emit(
                        "system_metrics.report", "{}"))
                    QTimer.singleShot(1800, lambda: chat_bridge.toolFinished.emit(
                        "system_metrics.report", True, 1240, ""))
                    QTimer.singleShot(2300, lambda: chat_bridge.messageAdded.emit(
                        "assistant", "The report is ready \u2014 see **System Report** below.\n\n```\nGPU: 59 \u00B0C \u00B7 75% VRAM \u00B7 29% util\n```", False))
                    QTimer.singleShot(2800, lambda: chat_bridge.streamUpdated.emit(" done"))
                elif sample == "d":
                    chat_bridge.messageAdded.emit("user", "Show me the GPU stats.", False)
                    chat_bridge.messageAdded.emit("assistant", "## GPU\n\n- **RTX 2070** \u00B7 59 \u00B0C \u00B7 75% VRAM\n- Driver 610.57.04\n\n[Full report](https://localhost:11434)", False)
                    QTimer.singleShot(600, lambda: window.setProperty("inspectorRequested", True))
                    QTimer.singleShot(800, lambda: chat_bridge.metricsUpdated.emit(
                        '{"gpu": {"name": "NVIDIA GeForce RTX 2070", "vram_total_mib": 8192, '
                        '"vram_used_mib": 6187, "driver": "610.57.04", "temperature_c": 59, '
                        '"utilization_pct": 29}, "memory": {"total_bytes": 16600000000, '
                        '"used_bytes": 6700000000}, "cpu": {"cores": 6}}'))
                    QTimer.singleShot(1000, lambda: chat_bridge.modelInfoChanged.emit())
                QTimer.singleShot(2600, grab_shot)

        QTimer.singleShot(900, seed if sample else grab_shot)

    # KDE integration: tray icon
    window = engine.rootObjects()[0]
    tray = TrayIcon(window=window, quit_cb=chat_bridge.quitApp)
    tray.show()

    # Run the merged event loop
    with loop:
        return loop.run_forever()


if __name__ == "__main__":
    sys.exit(main())