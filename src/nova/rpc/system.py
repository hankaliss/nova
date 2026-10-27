"""Local machine: live metrics and opening files with the desktop."""

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any


class SystemMixin:
    """Part of :class:`nova.rpc.core.NovaRpc`; state lives on the instance."""

    def refreshMetrics(self) -> None:
        async def _run() -> None:
            if self._agent is None:
                return
            out: dict[str, Any] = {}
            for name in ("linux.gpu_info", "linux.memory_info", "linux.cpu_info"):
                result = await self._agent.registry.execute(name)
                key = name.split(".")[1].removesuffix("_info")
                out[key] = result.data if result.success else {"error": result.error}
            out["ts"] = time.time()
            self.emit("metricsUpdated", json.dumps(out))

        asyncio.ensure_future(_run())

    def openPath(self, path: str) -> bool:
        """Open an existing file or folder with the system default handler.

        The path comes from the renderer, so refuse anything ``xdg-open``
        would *run* rather than show: executables and ``.desktop`` launchers.
        Returns whether it was opened.
        """
        import subprocess

        target = Path(path).expanduser()
        if not target.is_absolute() or not target.exists():
            return False
        target = target.resolve()
        if target.is_file() and (
            os.access(target, os.X_OK) or target.suffix.lower() == ".desktop"
        ):
            return False
        if not (target.is_file() or target.is_dir()):
            return False
        try:
            subprocess.Popen(["xdg-open", str(target)])
        except OSError:
            return False
        return True
