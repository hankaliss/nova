"""First-run setup wizard (issue #20, AD-040): status, model pull, done flag."""

import asyncio
import shutil
import time
from typing import Any

import httpx

from nova.llm.ollama import DEFAULT_MODEL
from nova.tools import linux
from nova.tools.web import SEARXNG_URL

# (minimum VRAM in MiB, model, note shown in the wizard), largest first.
# Source: docs/benchmarks.md; anything not measured says so.
MODEL_RECOMMENDATIONS: tuple[tuple[int, str, str], ...] = (
    (11500, "qwen3:8b", "Not benchmarked at this VRAM. On 8 GB it spills to the CPU and drops to ~5 tok/s."),
    (5800, DEFAULT_MODEL, "Benchmarked: runs fully on the GPU with an 8 GB card."),
    (0, "qwen3:1.7b", "Not benchmarked. Runs partly on the CPU without a 6 GB+ NVIDIA GPU."),
)

# Progress events at most this often: a pull reports every few KB.
_PROGRESS_EVERY_S = 0.25


def recommend_model(vram_mib: int | None) -> dict[str, str]:
    vram = vram_mib or 0
    for minimum, model, note in MODEL_RECOMMENDATIONS:
        if vram >= minimum:
            return {"model": model, "note": note}
    return {"model": MODEL_RECOMMENDATIONS[-1][1], "note": MODEL_RECOMMENDATIONS[-1][2]}


async def _searxng_reachable() -> bool:
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(SEARXNG_URL)
        return resp.status_code < 500
    except httpx.HTTPError:
        return False


class SetupMixin:
    """Part of :class:`nova.rpc.core.NovaRpc`; state lives on the instance."""

    _pull_task: "asyncio.Task | None" = None

    async def getSetupStatus(self) -> dict[str, Any]:
        provider = self._provider
        reachable = bool(provider) and await provider.is_available()
        models: list[str] = []
        if reachable:
            try:
                models = [m.name for m in await provider.list_models()]
            except Exception:  # noqa: BLE001 - status stays usable without the list
                reachable = False
        gpu = await asyncio.to_thread(linux._gpu_info)
        gpu = None if "error" in gpu else {"name": gpu["name"], "vram_total_mib": gpu["vram_total_mib"]}

        done = self._settings.get("onboarding_done") == "true"
        # Existing users (models already there, flag never set) skip the
        # wizard for good; a fresh install keeps it until it is closed.
        if not done and models and self._settings.get("onboarding_done", "") == "":
            self.markOnboardingDone()
            done = True

        return {
            "ollama": {"installed": shutil.which("ollama") is not None, "reachable": reachable},
            "gpu": gpu,
            "models": models,
            "recommended": recommend_model(gpu and gpu["vram_total_mib"]),
            "searxng": await _searxng_reachable(),
            "shortcut": self._settings.get("toggle_shortcut", ""),
            "onboarding_done": done,
            "pulling": self._pull_task is not None and not self._pull_task.done(),
        }

    def pullModel(self, name: str) -> None:
        """Start downloading ``name``; progress arrives as ``pull_progress``
        events and the end as one ``pull_done`` {model, ok, error}."""
        name = (name or "").strip()
        if not name or self._provider is None:
            self.emit("pull_done", {"model": name, "ok": False, "error": "Ollama is not reachable"})
            return
        if self._pull_task is not None and not self._pull_task.done():
            self.emit("pull_done", {"model": name, "ok": False, "error": "Another download is running"})
            return
        self._pull_task = asyncio.ensure_future(self._pull(name))

    async def _pull(self, name: str) -> None:
        last = 0.0
        try:
            async for p in self._provider.pull(name):
                now = time.monotonic()
                if now - last < _PROGRESS_EVERY_S and p.get("status") != "success":
                    continue
                last = now
                self.emit("pull_progress", {
                    "model": name,
                    "status": p.get("status", ""),
                    "completed": p.get("completed", 0),
                    "total": p.get("total", 0),
                })
        except asyncio.CancelledError:
            self.emit("pull_done", {"model": name, "ok": False, "error": "Cancelled"})
            return
        except Exception as e:  # noqa: BLE001 - shown to the user with Retry
            self.emit("pull_done", {"model": name, "ok": False, "error": _pull_error(e)})
            return
        self.listModels()
        self.selectModel(name)
        self.emit("pull_done", {"model": name, "ok": True, "error": ""})

    def cancelPull(self) -> None:
        if self._pull_task is not None and not self._pull_task.done():
            self._pull_task.cancel()

    def markOnboardingDone(self) -> None:
        self._settings["onboarding_done"] = "true"
        if self._store is not None:
            self._store.set_setting("onboarding_done", "true")


def _pull_error(e: Exception) -> str:
    text = str(e).strip()
    if isinstance(e, httpx.ConnectError):
        return "Ollama stopped responding. Is it still running?"
    if "file does not exist" in text or "not found" in text.lower():
        return f"Ollama doesn't know this model ({text})."
    if "no space left" in text.lower():
        return "Not enough disk space for this model."
    if "newer version of Ollama" in text:
        return "This model needs a newer Ollama. Update Ollama, then retry."
    return text or type(e).__name__
