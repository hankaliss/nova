"""Ollama models: list, select, describe."""

import asyncio
import json
import sys

from nova.llm.context_fit import FALLBACK_START, fit_context, step_down
from nova.tools import linux


class ModelsMixin:
    """Part of :class:`nova.rpc.core.NovaRpc`; state lives on the instance."""

    def listModels(self) -> None:
        async def _run() -> None:
            try:
                infos = await self._provider.list_models()
                self._model_infos = {info.name: info for info in infos}
                self.emit("modelsListed", [info.name for info in infos])
                self.emit("model_details", [
                    {
                        "name": info.name,
                        "size": info.size,
                        "parameter_size": info.parameter_size,
                        "quantization": info.quantization,
                        "context_length": info.context_length,
                        "capabilities": info.capabilities,
                    }
                    for info in infos
                ])
                # Emit model_info for the currently selected model
                current = self._agent.model if self._agent else ""
                info = self._model_infos.get(current) if current else None
                if info is not None:
                    self.emit("model_info", {
                        "model": info.name,
                        "details": {
                            "family": "",
                            "parameter_size": info.parameter_size,
                            "quantization": info.quantization,
                            "families": [],
                        },
                        "context_length": info.context_length,
                    })
                # Signal that Ollama is reachable
                self.emit("statusChanged", "connected")
            except Exception as e:
                self.emit("statusChanged", f"Error listing models: {e}")

        asyncio.ensure_future(_run())

    # ---- Context size (AD-028) -----------------------------------------

    def _auto_context(self) -> bool:
        return self._settings.get("num_ctx", "auto") == "auto"

    def _resolved_num_ctx(self) -> int:
        """num_ctx to send: the manual value, or the last fit for this model."""
        if not self._auto_context():
            return int(self._settings["num_ctx"])
        model = self._agent.model if self._agent is not None else ""
        cached = (
            self._store.get_setting(f"auto_ctx_{model}")
            if self._store is not None and model
            else None
        )
        return int(cached) if cached else FALLBACK_START

    def _emit_context(self, on_gpu: bool | None = None) -> None:
        if self._agent is None:
            return
        self.emit("context_size", {
            "model": self._agent.model or "",
            "num_ctx": self._agent.options.get("num_ctx"),
            "auto": self._auto_context(),
            "on_gpu": on_gpu,
            "fitting": self._fit_task is not None and not self._fit_task.done(),
        })

    def _apply_context(self) -> None:
        """Use the current context setting; in auto mode refit in the background."""
        if self._agent is None:
            return
        self._agent.options["num_ctx"] = self._resolved_num_ctx()
        if self._fit_task is not None and not self._fit_task.done():
            self._fit_task.cancel()
        self._fit_task = None
        if self._auto_context() and self._agent.model and self._provider is not None:
            self._fit_task = asyncio.ensure_future(self._fit_context(self._agent.model))
        self._emit_context()

    async def _free_vram_mib(self) -> int | None:
        """GPU memory not used by other programs (Ollama's own models count as free)."""
        gpu = await asyncio.to_thread(linux._gpu_info)
        if "error" in gpu:
            return None
        loaded = await self._provider.loaded_vram_mib()
        return gpu["vram_total_mib"] - gpu["vram_used_mib"] + loaded

    async def _fit_context(self, model: str) -> None:
        try:
            result = await fit_context(self._provider, model, self._free_vram_mib)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # Ollama down, model missing: keep the current value
            print(f"Nova: context fit for {model} failed: {e}", file=sys.stderr)
            return
        if self._agent is None or self._agent.model != model or not self._auto_context():
            return
        if self._store is not None:
            self._store.set_setting(f"auto_ctx_{model}", str(result.num_ctx))
        self._agent.options["num_ctx"] = result.num_ctx
        self._fit_task = None
        self._emit_context(on_gpu=result.on_gpu)

    def _context_fitting(self) -> bool:
        return self._fit_task is not None and not self._fit_task.done()

    async def _await_context_fit(self) -> None:
        """Wait (up to 60 s) for a running fit. Stop cancels the wait (the
        shielded fit carries on); a failed or slow fit is not an error."""
        task = self._fit_task
        if task is not None and not task.done():
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=60)
            except asyncio.CancelledError:
                if asyncio.current_task().cancelling():
                    raise  # Stop pressed
            except Exception:
                pass

    async def _check_context_spill(self) -> None:
        """After an answer: if the model spilled to the CPU, step down a size."""
        fitting = self._fit_task is not None and not self._fit_task.done()
        if self._agent is None or not self._auto_context() or fitting:
            return
        model = self._agent.model
        try:
            placement = await self._provider.placement(model)
        except Exception:
            return
        if not placement or placement[1] >= placement[0]:
            return
        smaller = step_down(int(self._agent.options.get("num_ctx", FALLBACK_START)))
        self._agent.options["num_ctx"] = smaller
        if self._store is not None:
            self._store.set_setting(f"auto_ctx_{model}", str(smaller))
        print(f"Nova: {model} spilled to the CPU; context now {smaller}", file=sys.stderr)
        self._emit_context(on_gpu=False)

    def selectModel(self, name: str) -> None:
        if self._agent is None:
            return
        self._agent.model = name
        self._settings["model"] = name
        self._apply_context()
        if self._store is not None:
            self._store.set_setting("model", name)
        self.emit("modelInfoChanged")
        self.emit("statusChanged", f"Model: {name}")
        # Emit model_info with raw Ollama data for the frontend
        info = getattr(self, "_model_infos", {}).get(name)
        if info is not None:
            self.emit("model_info", {
                "model": info.name,
                "details": {
                    "family": "",
                    "parameter_size": info.parameter_size,
                    "quantization": info.quantization,
                    "families": [],
                },
                "context_length": info.context_length,
            })

    def currentModel(self) -> str:
        return self._agent.model if self._agent and self._agent.model else "(default)"

    def modelInfo(self) -> str:
        if self._agent is None:
            return "{}"
        info = getattr(self, "_model_infos", {}).get(self._agent.model or "")
        if info is None:
            return "{}"
        return json.dumps({
            "name": info.name,
            "parameter_size": info.parameter_size,
            "quantization": info.quantization,
            "context_length": info.context_length,
            "size_bytes": info.size,
        })
