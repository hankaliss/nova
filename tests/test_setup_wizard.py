"""First-run setup wizard backend (issue #20): status, pull, done flag."""

import asyncio
import json

import httpx
import pytest

from nova.llm.ollama import DEFAULT_MODEL, OllamaProvider
from nova.llm.models import ModelInfo
from nova.rpc import setup as setup_rpc
from nova.rpc.setup import recommend_model

from .test_rpc import FakeStore, rpc  # noqa: F401 - fixture


class FakeProvider:
    def __init__(self, reachable=True, models=(), pull_lines=(), pull_error=None, pull_delay=0.0):
        self.reachable = reachable
        self.models = list(models)
        self.pull_lines = list(pull_lines)
        self.pull_error = pull_error
        self.pull_delay = pull_delay
        self.pulled: list[str] = []

    async def is_available(self):
        return self.reachable

    async def list_models(self):
        return [ModelInfo(name=m) for m in self.models]

    async def pull(self, model):
        self.pulled.append(model)
        for line in self.pull_lines:
            await asyncio.sleep(self.pull_delay)
            yield line
        if self.pull_error:
            raise self.pull_error
        self.models.append(model)


@pytest.fixture
def wizard(rpc, monkeypatch):  # noqa: F811 - pytest fixture injection
    nova, transport = rpc
    nova._store = FakeStore()
    nova._provider = FakeProvider()
    nova.listModels = lambda: None
    nova.selected = []
    nova.selectModel = lambda name: nova.selected.append(name)
    monkeypatch.setattr(setup_rpc.linux, "_gpu_info",
                        lambda: {"name": "RTX 2070", "vram_total_mib": 8192, "vram_used_mib": 900})
    monkeypatch.setattr(setup_rpc.shutil, "which", lambda name: "/usr/local/bin/ollama")

    async def no_searxng():
        return False

    monkeypatch.setattr(setup_rpc, "_searxng_reachable", no_searxng)
    return nova, transport


def events(transport, name):
    return [m["params"][0] for m in transport.sent if m.get("event") == name]


@pytest.mark.parametrize("vram,model", [
    (None, "qwen3.5:2b"), (4096, "qwen3.5:2b"), (6144, DEFAULT_MODEL), (8192, DEFAULT_MODEL),
    (12288, "qwen3.5:9b"), (24576, "qwen3.5:9b"),
])
def test_recommendation_by_vram(vram, model):
    rec = recommend_model(vram)
    assert rec["model"] == model and rec["note"]


@pytest.mark.asyncio
async def test_status_fresh_install(wizard):
    nova, _ = wizard
    nova._provider.reachable = False
    status = await nova.getSetupStatus()
    assert status["ollama"] == {"installed": True, "reachable": False}
    assert status["gpu"] == {"name": "RTX 2070", "vram_total_mib": 8192}
    assert status["recommended"]["model"] == DEFAULT_MODEL
    assert status["models"] == [] and status["onboarding_done"] is False
    assert status["searxng"] is False and status["pulling"] is False


@pytest.mark.asyncio
async def test_status_without_gpu_or_ollama_binary(wizard, monkeypatch):
    nova, _ = wizard
    monkeypatch.setattr(setup_rpc.linux, "_gpu_info", lambda: {"error": "unable to query NVIDIA GPU"})
    monkeypatch.setattr(setup_rpc.shutil, "which", lambda name: None)
    status = await nova.getSetupStatus()
    assert status["gpu"] is None and status["ollama"]["installed"] is False
    assert status["recommended"]["model"] == "qwen3.5:2b"


@pytest.mark.asyncio
async def test_existing_user_with_models_is_marked_done(wizard):
    nova, _ = wizard
    nova._provider.models = ["qwen3:4b"]
    status = await nova.getSetupStatus()
    assert status["onboarding_done"] is True
    assert nova._store.get_setting("onboarding_done") == "true"


@pytest.mark.asyncio
async def test_mark_done_persists(wizard):
    nova, _ = wizard
    nova.markOnboardingDone()
    assert nova._settings["onboarding_done"] == "true"
    assert nova._store.get_setting("onboarding_done") == "true"
    assert (await nova.getSetupStatus())["onboarding_done"] is True


@pytest.mark.asyncio
async def test_pull_streams_progress_then_selects_model(wizard):
    nova, transport = wizard
    nova._provider.pull_lines = [
        {"status": "pulling manifest"},
        {"status": "pulling abc", "completed": 10, "total": 100},
        {"status": "success"},
    ]
    nova.pullModel("qwen3:4b")
    await nova._pull_task
    progress = events(transport, "pull_progress")
    assert progress[0] == {"model": "qwen3:4b", "status": "pulling manifest", "completed": 0, "total": 0}
    assert progress[-1]["status"] == "success"
    assert events(transport, "pull_done") == [{"model": "qwen3:4b", "ok": True, "error": ""}]
    assert nova.selected == ["qwen3:4b"]


@pytest.mark.asyncio
async def test_pull_error_is_readable(wizard):
    nova, transport = wizard
    nova._provider.pull_error = RuntimeError("pull model manifest: file does not exist")
    nova.pullModel("nope:1b")
    await nova._pull_task
    done = events(transport, "pull_done")[0]
    assert done["ok"] is False and "doesn't know this model" in done["error"]
    assert nova.selected == []


@pytest.mark.asyncio
async def test_cancel_pull(wizard):
    nova, transport = wizard
    nova._provider.pull_lines = [{"status": "pulling", "completed": i, "total": 9} for i in range(9)]
    nova._provider.pull_delay = 0.05
    nova.pullModel("qwen3:4b")
    await asyncio.sleep(0.02)
    assert (await nova.getSetupStatus())["pulling"] is True
    nova.cancelPull()
    await asyncio.gather(nova._pull_task, return_exceptions=True)
    assert events(transport, "pull_done") == [{"model": "qwen3:4b", "ok": False, "error": "Cancelled"}]
    assert nova.selected == []


@pytest.mark.asyncio
async def test_second_pull_is_refused_while_one_runs(wizard):
    nova, transport = wizard
    nova._provider.pull_lines = [{"status": "pulling"}] * 3
    nova._provider.pull_delay = 0.05
    nova.pullModel("a:1b")
    nova.pullModel("b:1b")
    assert events(transport, "pull_done") == [{"model": "b:1b", "ok": False, "error": "Another download is running"}]
    nova.cancelPull()
    await asyncio.gather(nova._pull_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_provider_pull_parses_stream_and_errors():
    lines = [{"status": "pulling manifest"}, {"status": "pulling x", "completed": 5, "total": 10}, {"status": "success"}]

    def handler(request):
        body = json.loads(request.content)
        if body["model"] == "bad":
            return httpx.Response(404, json={"error": "pull model manifest: file does not exist"})
        if body["model"] == "midway":
            return httpx.Response(200, text=json.dumps(lines[0]) + "\n" + json.dumps({"error": "disk full"}) + "\n")
        return httpx.Response(200, text="".join(json.dumps(x) + "\n" for x in lines))

    provider = OllamaProvider()
    provider._client = httpx.AsyncClient(base_url="http://ollama", transport=httpx.MockTransport(handler))
    provider._model_info_at = 123.0
    assert [x async for x in provider.pull("qwen3:4b")] == lines
    assert provider._model_info_at == 0.0
    with pytest.raises(RuntimeError, match="file does not exist"):
        [x async for x in provider.pull("bad")]
    with pytest.raises(RuntimeError, match="disk full"):
        [x async for x in provider.pull("midway")]
    await provider.close()
