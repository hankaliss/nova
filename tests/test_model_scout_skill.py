"""model_scout skill (issue #21): parsing, fit, scoring, ranking and the
benchmark loop against a fake Ollama. No network, no GPU."""

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from nova.skills import host
from nova.skills.loader import SkillLoader
from nova.tools.registry import ToolRegistry
from nova.tools.report import ARTIFACT_KEY
from nova.tools.tool import Tool

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "ollama_com"


@pytest.fixture
def ms():
    """The skill's module, as the loader imported it."""
    loader = SkillLoader(ROOT / "skills")
    loader.register_all(ToolRegistry())
    return next(m for m in loader._modules.values() if m.__name__ == "nova_skill_model_scout")


@pytest.fixture(autouse=True)
def _clean_host():
    yield
    host.attach(None)
    for name in ("ollama_url", "current_model", "system_prompt", "select_model"):
        host.provide(name, None)


def test_discovered_with_risks():
    data = json.loads(SkillLoader(ROOT / "skills").to_json())
    skill = next(s for s in data if s["name"] == "model_scout")
    assert {t["name"]: t["risk"] for t in skill["tools"]} == {
        "model_scout.hardware": "read", "model_scout.lookup": "read", "model_scout.pull": "write",
        "model_scout.benchmark": "write", "model_scout.set_default": "write", "model_scout.remove": "destructive",
    }


# ---- ollama.com parsing (fixtures cut from the real pages) ----------------


def test_parse_tags_page_skips_sizeless_cloud_row(ms):
    rows = ms.parse_tags_page((FIXTURES / "gemma4_tags.html").read_text())
    assert [r["name"] for r in rows] == ["gemma4:latest", "gemma4:e4b", "gemma4:e4b-it-q8_0", "gemma4:12b"]
    assert rows[1] == {"name": "gemma4:e4b", "size": "6.6GB", "context": "128K"}


def test_parse_model_page_capabilities_and_required_version(ms):
    info = ms.parse_model_page((FIXTURES / "tev1.html").read_text())
    assert info == {"capabilities": ["thinking", "tools"], "requires": "0.35"}
    assert ms.parse_model_page("<p>no badges</p>") == {"capabilities": [], "requires": ""}


def test_parse_search_page_drops_cloud_only(ms):
    found = ms.parse_search_page((FIXTURES / "search_tools.html").read_text())
    assert found == [
        {"name": "bonsai-2", "capabilities": ["thinking", "tools", "vision"], "sizes": ["2b", "4b", "9b", "27b"]},
        {"name": "tev1", "capabilities": ["thinking", "tools"], "sizes": ["0.8b", "4b"]},
    ]


def test_fit_levels(ms):
    gpu = {"vram_total_mib": 8192, "vram_used_mib": 1548}
    assert ms._fits(ms._mib("3.3GB"), gpu) == "yes"
    assert ms._fits(ms._mib("6.6GB"), gpu) == "maybe"  # gemma4:e4b: runs, but measure it
    assert ms._fits(ms._mib("9.3GB"), gpu) == "no"
    assert ms._fits(1000, {}).startswith("unknown")
    assert ms._mib("834MB") == 795


# ---- scoring and ranking ---------------------------------------------------


def call(tool, **args):
    return {"tool_calls": [{"function": {"name": tool, "arguments": args}}], "content": ""}


@pytest.mark.parametrize("message,accepted,mention,ok,why", [
    (call("linux.gpu_info"), ("linux.gpu_info",), "", True, ""),
    (call("services.list", match="ollama"), ("services.status", "services.list"), "ollama", True, ""),
    (call("web.search", query="weather"), ("web.search",), "plasma", False, "unexpected arguments"),
    (call("services.status"), ("services.restart",), "", False, "called services.status"),
    (call("services.restart"), ("services.restart",), "", False, "without name"),
    ({"content": "Sure, I'll check."}, ("linux.gpu_info",), "", False, "answered without a tool"),
    ({"content": "A thread shares memory."}, None, "", True, ""),
    (call("web.search", query="x"), None, "", False, "called web.search instead of answering"),
    ({"content": "  "}, None, "", False, "empty answer"),
    (call("filesystem.search", **{"pattern": "PKGBUILD"}), ("filesystem.search",), "pkgbuild", True, ""),
])
def test_score_case(ms, message, accepted, mention, ok, why):
    got_ok, got_why = ms.score_case(message, accepted, mention, {"services.restart": ["name"]})
    assert got_ok is ok and why in got_why


def result(passed, total=12, gpu=100, median=2.0, tps=80.0):
    return {"tools": [{"ok": i < passed} for i in range(total)], "median_s": median,
            "speed": [{"gpu_pct": gpu, "gen_tps": tps}]}


def test_rank_cuts_then_orders(ms):
    results = {
        "slow": result(12, median=7.0),
        "fast": result(11, median=2.5, tps=70),
        "faster-gen": result(12, median=2.5, tps=90),
        "sloppy": result(9),
        "spills": result(12, gpu=74),
        "broken": {"error": "not installed (pull it first)"},
    }
    ranked, dropped = ms.rank(results, 12)
    assert ranked == ["faster-gen", "fast", "slow"]
    assert dropped == {"sloppy": "tool calls 9/12", "spills": "74% on the GPU at 8K: spills to the CPU",
                       "broken": "not installed (pull it first)"}


# ---- tools against a fake Ollama ------------------------------------------


class FakeOllama:
    """Enough of Ollama's HTTP API for the skill."""

    def __init__(self, models=("qwen3:4b", "gemma4:e4b"), loaded=("qwen3:4b", 12288), version="0.32.14"):
        self.models = {m: {"name": m, "size": 2_500_000_000} for m in models}
        self.loaded = [loaded] if loaded else []
        self.version = version
        self.requests: list[tuple[str, dict]] = []
        self.chat_error_for: set[str] = set()

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        self.requests.append((request.url.path, body))
        path = request.url.path
        if path == "/api/version":
            return httpx.Response(200, json={"version": self.version})
        if path == "/api/tags":
            return httpx.Response(200, json={"models": list(self.models.values())})
        if path == "/api/ps":
            return httpx.Response(200, json={"models": [
                {"name": m, "model": m, "size": 4_000_000_000, "size_vram": 4_000_000_000, "context_length": ctx}
                for m, ctx in self.loaded]})
        if path == "/api/generate":
            if body.get("keep_alive") == 0:
                self.loaded = [x for x in self.loaded if x[0] != body["model"]]
            else:
                self.loaded = [(body["model"], (body.get("options") or {}).get("num_ctx", 0))]
            return httpx.Response(200, json={"done": True})
        if path == "/api/delete":
            return httpx.Response(200 if self.models.pop(body["model"], None) else 404, json={})
        if path == "/api/chat":
            model = body["model"]
            if model in self.chat_error_for:
                return httpx.Response(200, json={"error": "model crashed"})
            self.loaded = [(model, body.get("options", {}).get("num_ctx", 0))]
            if not body.get("messages"):
                return httpx.Response(200, json={"done": True})  # warm-up
            if "tools" not in body:  # speed run
                return httpx.Response(200, json={
                    "message": {"content": "summary"}, "load_duration": 2e9,
                    "prompt_eval_count": 2400, "prompt_eval_duration": 1e9,
                    "eval_count": 256, "eval_duration": 3.2e9})
            last = body["messages"][-1]
            if last["role"] == "tool":
                return httpx.Response(200, json={"message": {"content": "1,548 MiB of 8 GB in use."}})
            prompt = last["content"]
            answer = {"How much GPU": call("linux.gpu_info"), "Restart the": call("services.restart", name="bluetooth"),
                      "Search the web": call("web.search", name="KDE Plasma release")}
            if model == "gemma4:e4b":  # this one calls a tool to say hello
                answer["Hi!"] = call("web.search", name="who am I")
            for start, msg in answer.items():
                if prompt.startswith(start):
                    return httpx.Response(200, json={"message": msg})
            return httpx.Response(200, json={"message": {"content": "An answer."}})
        return httpx.Response(404, json={"error": "unknown"})


@pytest.fixture
def ollama(ms, monkeypatch, tmp_path):
    fake = FakeOllama()

    def client(timeout=30):
        return httpx.AsyncClient(base_url="http://ollama", transport=httpx.MockTransport(fake.handler))

    monkeypatch.setattr(ms, "_client", client)
    monkeypatch.setattr(ms, "_gpu", lambda: {"name": "RTX 2070", "vram_total_mib": 8192, "vram_used_mib": 5400})
    monkeypatch.setattr(ms, "RESULTS_FILE", tmp_path / "benchmarks.json")
    monkeypatch.setattr(ms.asyncio, "sleep", _no_sleep)
    reg = ToolRegistry()
    for name in ("linux.gpu_info", "services.restart", "web.search"):
        reg.register(Tool(name=name, description="", implementation=lambda: None,
                          parameters={"type": "object", "properties": {"name": {"type": "string"}},
                                      "required": ["name"] if name == "services.restart" else []}))
    host.attach(reg)
    host.provide("system_prompt", lambda: "You are Nova.")
    return fake


async def _no_sleep(*_):
    return None


def test_benchmark_runs_ranks_reports_and_restores_chat_model(ms, ollama, tmp_path):
    result = asyncio.run(ms._benchmark(["qwen3:4b", "gemma4:e4b", "missing:1b"]))
    report = result.pop(ARTIFACT_KEY)["html"]
    # Only cases for registered tools, plus the two no-tool ones: gpu, web, restart.
    assert result["cases"] == 5
    assert result["ranked"] == ["qwen3:4b"] and result["winner"] == "qwen3:4b"
    assert result["dropped"] == {"gemma4:e4b": "tool calls 4/5", "missing:1b": "not installed (pull it first)"}
    assert "tools 5/5" in result["per_model"]["qwen3:4b"]
    assert "Recommended." in report and "Misses" in report and "called web.search instead of answering" in report
    # The chat model that was loaded before is back, at its context size.
    assert ollama.loaded == [("qwen3:4b", 12288)]
    saved = json.loads((tmp_path / "benchmarks.json").read_text())
    assert saved[0]["ranked"] == ["qwen3:4b"] and saved[0]["ollama"] == "0.32.14"


def test_benchmark_restores_chat_model_after_an_error(ms, ollama, monkeypatch):
    async def boom(*args, **kwargs):
        raise RuntimeError("GPU fell off")

    monkeypatch.setattr(ms, "_tool_cases", boom)
    with pytest.raises(RuntimeError, match="GPU fell off"):
        asyncio.run(ms._benchmark(["gemma4:e4b"]))
    assert ollama.loaded == [("qwen3:4b", 12288)]


def test_benchmark_model_error_is_reported_not_raised(ms, ollama):
    ollama.chat_error_for.add("gemma4:e4b")
    result = asyncio.run(ms._benchmark(["gemma4:e4b"], quick=True))
    assert result["dropped"] == {"gemma4:e4b": "model crashed"} and result["winner"] is None


def test_quick_benchmark_uses_fewer_cases(ms, ollama):
    result = asyncio.run(ms._benchmark(["qwen3:4b"], quick=True))
    assert result["cases"] == 4 and result["winner"] == "qwen3:4b"  # quick set ∩ registered: gpu, web, 2 no-tool
    speed_runs = [b for p, b in ollama.requests if p == "/api/chat" and "think" in b]
    assert [b["options"]["num_ctx"] for b in speed_runs] == [8192]


def test_pull_refuses_model_needing_newer_ollama(ms, ollama, monkeypatch):
    async def requires(model):
        return "0.35"

    monkeypatch.setattr(ms, "_requires", requires)
    with pytest.raises(ValueError, match="needs Ollama 0.35 or later.*installed: 0.32.14"):
        asyncio.run(ms._pull("tev1:4b"))
    assert not any(p == "/api/pull" for p, _ in ollama.requests)


def test_set_default_and_remove(ms, ollama):
    chosen = []
    host.provide("select_model", chosen.append)
    host.provide("current_model", lambda: "qwen3:4b")
    assert asyncio.run(ms._set_default("gemma4:e4b")) == {"default_model": "gemma4:e4b"}
    assert chosen == ["gemma4:e4b"]
    with pytest.raises(ValueError, match="not installed"):
        asyncio.run(ms._set_default("nope:1b"))
    with pytest.raises(ValueError, match="chat model in use"):
        asyncio.run(ms._remove("qwen3:4b"))
    assert asyncio.run(ms._remove("gemma4:e4b")) == {"removed": "gemma4:e4b"}
    with pytest.raises(ValueError, match="not installed"):
        asyncio.run(ms._remove("gemma4:e4b"))


def test_host_services():
    with pytest.raises(RuntimeError, match="not available"):
        host.service("current_model")
    host.provide("current_model", lambda: "qwen3:4b")
    assert host.service("current_model")() == "qwen3:4b"
    host.provide("current_model", None)
    with pytest.raises(RuntimeError):
        host.service("current_model")
    reg = ToolRegistry()
    hidden = Tool(name="mcp.x.hidden", description="", implementation=lambda: None, model_visible=False)
    reg.register(hidden)
    reg.register(Tool(name="linux.gpu_info", description="", implementation=lambda: None))
    host.attach(reg)
    assert [t["function"]["name"] for t in host.model_tool_schemas()] == ["linux.gpu_info"]
