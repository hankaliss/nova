"""Model scout: find, download and benchmark Ollama models for this machine (issue #21).

The model plans (web search, shortlist, the user's picks); the code measures,
so the numbers can be trusted (AD-041). Benchmarks use Nova's real tool
schemas and system prompt, unload the chat model while they run (a tool runs
between model turns, so nothing is generating) and load it back at the end.
"""

import asyncio
import html
import json
import math
import re
import statistics
import time
from pathlib import Path

import httpx

from nova.memory.store import DEFAULT_DB_PATH
from nova.skills import host
from nova.tools import linux
from nova.tools.report import Finding, Report, Stat, plural, with_artifact
from nova.tools.tool import RiskLevel, Tool

LIBRARY = "https://ollama.com"
DEFAULT_OLLAMA = "http://localhost:11434"
RESULTS_FILE = DEFAULT_DB_PATH.parent / "benchmarks.json"
KV_8K_MIB = 1300  # KV cache and buffers at an 8K context, measured on 4–9B models
DESKTOP_MIB = 700  # what the desktop keeps on the GPU when nvidia-smi can't say
MIN_TOOL_SCORE = 10 / 12  # ranking cut: share of tool cases passed
LOOKUP_LIMIT = 30

# (prompt, accepted tools or None for "answer without a tool", text the args must mention)
CASES: list[tuple[str, tuple[str, ...] | None, str]] = [
    ("How much GPU memory is in use right now?", ("linux.gpu_info",), ""),
    ("What's the CPU temperature?", ("linux.temperatures",), ""),
    ("Is the ollama service running?", ("services.status", "services.list"), "ollama"),
    ("Something feels broken since I booted. Check the system for problems.", ("system_doctor.check",), ""),
    ("What did I change in ~/Projects/AI/nova? Suggest a commit message.", ("git_companion.status",), "nova"),
    ("Find files named PKGBUILD in my home folder.", ("filesystem.search",), "pkgbuild"),
    ("What's taking up all the space on my disk?", ("disk_cleaner.scan", "linux.disk_usage"), ""),
    ("Search the web for the latest KDE Plasma release.", ("web.search",), "plasma"),
    ("Remember that my favourite editor is Helix.", ("memory.remember",), "helix"),
    ("Restart the bluetooth service.", ("services.restart",), "bluetooth"),
    ("Explain the difference between a process and a thread in two sentences.", None, ""),
    ("Hi! Who are you?", None, ""),
]
QUICK_CASES = (0, 3, 5, 7, 10, 11)
FILLER = ("The quick brown fox jumps over the lazy dog while the system monitors "
          "CPU load, memory pressure and disk activity on a Linux desktop. ")
_GPU_RESULT = {"name": "NVIDIA GeForce RTX 2070", "vram_total_mib": 8192, "vram_used_mib": 1548,
               "temperature_c": 47, "utilization_pct": 3}

_TAG_ROW = re.compile(
    r'href="/library/(?P<name>[\w.\-]+:[\w.\-]+)"\s+class="md:hidden(?:(?!href="/library/).)*?</span>\s*•\s*'
    r'(?P<size>[\d.]+\s*[KMGT]B)(?:\s*-\s*[\d.]+\s*[KMGT]B)?\s*•\s*(?P<ctx>[\d.]+[KM])\s+context', re.S)
_BADGE = re.compile(r'<span\s+class="[^"]*rounded-md[^"]*">\s*([^<]+?)\s*</span>')
_REQUIRES = re.compile(r"requires\s*(?:<a[^>]*>|\[)?\s*Ollama\s+v?(\d+(?:\.\d+){1,2})", re.I)
_SEARCH_ITEM = re.compile(r'<a href="/library/(?P<name>[\w.\-]+)" class="group grid(?P<body>.*?)</li>', re.S)
_SIZE_BADGE = re.compile(r"^(?:e?\d+(?:\.\d+)?[bm]|\d+x\d+b)$", re.I)
_CAPABILITIES = {"tools", "thinking", "vision", "audio", "embedding", "cloud"}


# ---- Helpers ---------------------------------------------------------------


def _ollama_url() -> str:
    try:
        return host.service("ollama_url")() or DEFAULT_OLLAMA
    except RuntimeError:
        return DEFAULT_OLLAMA


def _client(timeout: float | None = 30) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=_ollama_url(), timeout=httpx.Timeout(timeout, connect=5.0))


def _version(text: str) -> tuple[int, ...]:
    return tuple(int(p) for p in re.findall(r"\d+", text)[:3]) or (0,)


def _mib(size: str) -> int:
    """ollama.com sizes ("6.6GB") are decimal; VRAM is reported in MiB."""
    number, unit = float(re.match(r"[\d.]+", size).group()), size.strip()[-2:].upper()
    return int(number * {"KB": 1e3, "MB": 1e6, "GB": 1e9, "TB": 1e12}[unit] / 2 ** 20)


async def _ollama_version(client: httpx.AsyncClient) -> str:
    try:
        return (await client.get("/api/version")).json().get("version", "")
    except (httpx.HTTPError, ValueError):
        return ""


async def _installed(client: httpx.AsyncClient) -> dict[str, dict]:
    resp = await client.get("/api/tags")
    resp.raise_for_status()
    return {m["name"]: m for m in resp.json().get("models", [])}


def _gpu() -> dict:
    info = linux._gpu_info()
    return {} if "error" in info else info


# ---- hardware() ------------------------------------------------------------


async def _hardware() -> dict:
    gpu, mem, cpu = await asyncio.gather(asyncio.to_thread(_gpu), asyncio.to_thread(linux._memory_info),
                                         asyncio.to_thread(linux._cpu_info))
    async with _client() as client:
        version = await _ollama_version(client)
        try:
            installed = await _installed(client)
        except httpx.HTTPError:
            installed = None
    return {
        "gpu": {k: gpu[k] for k in ("name", "vram_total_mib", "vram_used_mib")} if gpu else None,
        "ram_gb": round(mem.get("total_bytes", 0) / 1e9, 1) if "error" not in mem else None,
        "cpu": {k: cpu.get(k) for k in ("model", "cores") if cpu.get(k)},
        "ollama_version": version or "not reachable",
        "installed_models": None if installed is None else [
            {"name": n, "size_gb": round(m.get("size", 0) / 1e9, 1)} for n, m in installed.items()
        ],
        "fit_rule": f"a model fits when its download size + ~{KV_8K_MIB} MiB (8K context) "
                    "+ what the desktop already uses is under the VRAM; downloads with vision or "
                    "audio parts can overstate it, so benchmark the close calls",
    }


# ---- lookup() --------------------------------------------------------------


def parse_tags_page(page: str) -> list[dict]:
    """Tag rows of ollama.com/library/<name>/tags: name, download size, context."""
    rows, seen = [], set()
    for m in _TAG_ROW.finditer(page):
        if m["name"] in seen:
            continue
        seen.add(m["name"])
        rows.append({"name": m["name"], "size": m["size"].replace(" ", ""), "context": m["ctx"]})
    return rows


def parse_model_page(page: str) -> dict:
    """Capability badges and the minimum Ollama version stated in the readme."""
    badges = {b.strip().lower() for b in _BADGE.findall(page)}
    requires = _REQUIRES.search(html.unescape(page))
    # "cloud" only says a cloud tag exists next to the local ones.
    return {"capabilities": sorted(badges & _CAPABILITIES - {"cloud"}), "requires": requires[1] if requires else ""}


def parse_search_page(page: str) -> list[dict]:
    """Model families of ollama.com/search: name, capabilities, parameter sizes."""
    out = []
    for m in _SEARCH_ITEM.finditer(page):
        badges = [b.strip() for b in _BADGE.findall(m["body"])]
        caps = {b.lower() for b in badges} & _CAPABILITIES
        sizes = [b for b in badges if _SIZE_BADGE.match(b)]
        if "cloud" in caps and not sizes:
            continue  # runs on ollama.com only
        out.append({"name": m["name"], "capabilities": sorted(caps - {"cloud"}), "sizes": sizes})
    return out


def _fits(size_mib: int, gpu: dict) -> str:
    """"yes", "maybe" or "no" at an 8K context. The download can overstate
    what loads on the GPU (vision/audio parts, per-layer embeddings: gemma4:e4b
    is 6.6 GB and runs fully on 8 GB), so "maybe" means: measure it."""
    if not gpu:
        return "unknown (no NVIDIA GPU)"
    budget = gpu["vram_total_mib"] - gpu.get("vram_used_mib", DESKTOP_MIB) - KV_8K_MIB
    if size_mib <= budget:
        return "yes"
    return "maybe" if size_mib <= budget * 1.4 else "no"


async def _lookup(models: list[str] | None = None) -> dict:
    gpu = await asyncio.to_thread(_gpu)
    async with _client() as ollama:
        version = await _ollama_version(ollama)
        try:
            installed = set(await _installed(ollama))
        except httpx.HTTPError:
            installed = set()
    async with httpx.AsyncClient(base_url=LIBRARY, timeout=20, follow_redirects=True) as web:
        if not models:
            resp = await web.get("/search", params={"c": "tools"})
            resp.raise_for_status()
            found = parse_search_page(resp.text)
            return {"source": "ollama.com/search?c=tools", "models": found[:LOOKUP_LIMIT],
                    "next": "call lookup with the names that look promising to get sizes and fit"}

        async def one(name: str) -> dict:
            base, _, wanted = name.partition(":")
            try:
                tags_page, model_page = await asyncio.gather(web.get(f"/library/{base}/tags"),
                                                             web.get(f"/library/{base}"))
                tags_page.raise_for_status()
            except httpx.HTTPError as e:
                return {"name": name, "error": f"not found on ollama.com ({e.__class__.__name__})"}
            info = parse_model_page(model_page.text)
            needs = info["requires"] if info["requires"] and version and \
                _version(info["requires"]) > _version(version) else ""
            tags = []
            for row in parse_tags_page(tags_page.text):
                tag = row["name"].split(":", 1)[1]
                # Plain tags only (4b, e4b, latest): quantization variants are noise here.
                if (wanted and tag != wanted) or (not wanted and ("-" in tag or tag == "cloud")):
                    continue
                size_mib = _mib(row["size"])
                tags.append({"model": row["name"], "download": row["size"], "context": row["context"],
                             "fits_vram": _fits(size_mib, gpu), "installed": row["name"] in installed
                             or (tag == "latest" and base in installed)})
            out = {"name": base, "capabilities": info["capabilities"], "tags": tags}
            if needs:
                out["needs_ollama"] = f">= {needs} (installed {version})"
            if "tools" not in info["capabilities"]:
                out["warning"] = "no native tool calling: Nova would fall back to text-mode tools"
            return out

        results = await asyncio.gather(*(one(n) for n in models[:10]))
    return {"ollama_version": version, "vram_total_mib": gpu.get("vram_total_mib"), "models": list(results)}


# ---- pull() / set_default() / remove() --------------------------------------


async def _requires(model: str) -> str:
    try:
        async with httpx.AsyncClient(base_url=LIBRARY, timeout=15, follow_redirects=True) as web:
            return parse_model_page((await web.get(f"/library/{model.split(':')[0]}")).text)["requires"]
    except httpx.HTTPError:
        return ""


async def _pull(model: str) -> dict:
    model = model.strip()
    async with _client(timeout=None) as client:
        version, requires = await asyncio.gather(_ollama_version(client), _requires(model))
        if requires and version and _version(requires) > _version(version):
            raise ValueError(f"{model} needs Ollama {requires} or later (installed: {version}). "
                             "Update Ollama first: curl -fsSL https://ollama.com/install.sh | sh")
        started, last = time.monotonic(), {}
        async with client.stream("POST", "/api/pull", json={"model": model, "stream": True}) as resp:
            if resp.status_code != 200:
                body = (await resp.aread()).decode("utf-8", "replace")
                raise ValueError(f"Ollama refused {model}: {body.strip()[:200]}")
            async for line in resp.aiter_lines():
                if line:
                    last = json.loads(line)
                    if last.get("error"):
                        raise ValueError(f"pulling {model} failed: {last['error']}")
        size = (await _installed(client)).get(model, {}).get("size", 0)
    return {"model": model, "status": last.get("status", ""), "size_gb": round(size / 1e9, 1),
            "seconds": round(time.monotonic() - started)}


async def _set_default(model: str) -> dict:
    async with _client() as client:
        if model not in await _installed(client):
            raise ValueError(f"{model} is not installed; pull it first")
    host.service("select_model")(model)
    return {"default_model": model}


async def _remove(model: str) -> dict:
    current = ""
    try:
        current = host.service("current_model")()
    except RuntimeError:
        pass
    if model == current:
        raise ValueError(f"{model} is the chat model in use; set another default first")
    async with _client() as client:
        resp = await client.request("DELETE", "/api/delete", json={"model": model})
        if resp.status_code == 404:
            raise ValueError(f"{model} is not installed")
        resp.raise_for_status()
    return {"removed": model}


# ---- benchmark() -----------------------------------------------------------


def score_case(message: dict, accepted: tuple[str, ...] | None, mention: str,
               required: dict[str, list[str]]) -> tuple[bool, str]:
    """(passed, why) for one tool case, from Ollama's reply message."""
    calls = message.get("tool_calls") or []
    names = [c.get("function", {}).get("name", "") for c in calls]
    if accepted is None:
        if calls:
            return False, f"called {', '.join(names)} instead of answering"
        return (True, "") if (message.get("content") or "").strip() else (False, "empty answer")
    if not calls:
        return False, "answered without a tool: " + (message.get("content") or "")[:70].replace("\n", " ")
    fn = calls[0].get("function", {})
    args = fn.get("arguments") or {}
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            args = {}
    if fn.get("name") not in accepted:
        return False, f"called {fn.get('name')} (expected {' or '.join(accepted)})"
    missing = [k for k in required.get(fn["name"], []) if k not in args]
    if missing:
        return False, f"{fn['name']} without {', '.join(missing)}"
    if mention and mention not in json.dumps(args).lower():
        return False, f"{fn['name']} with unexpected arguments {json.dumps(args)[:80]}"
    return True, ""


async def _speed(client: httpx.AsyncClient, model: str, num_ctx: int) -> dict:
    await _unload(client, model)
    resp = await client.post("/api/chat", json={
        "model": model, "stream": False, "think": False,
        "messages": [{"role": "user", "content": FILLER * 90 + "\nSummarise the text above in detail."}],
        "options": {"num_ctx": num_ctx, "temperature": 0, "num_predict": 256, "seed": 1},
    })
    r = resp.json()
    if "error" in r:
        return {"num_ctx": num_ctx, "error": str(r["error"])[:200]}
    loaded = next((m for m in (await client.get("/api/ps")).json().get("models", [])
                   if m.get("name") == model or m.get("model") == model), {})
    size, vram = loaded.get("size", 0), loaded.get("size_vram", 0)
    gpu = await asyncio.to_thread(_gpu)
    return {
        "num_ctx": num_ctx,
        "load_s": round(r.get("load_duration", 0) / 1e9, 1),
        "prompt_tps": round(r["prompt_eval_count"] / (r["prompt_eval_duration"] / 1e9))
        if r.get("prompt_eval_duration") else None,
        "gen_tps": round(r["eval_count"] / (r["eval_duration"] / 1e9), 1) if r.get("eval_duration") else None,
        "gpu_pct": round(100 * vram / size) if size else None,
        "vram_used_mib": gpu.get("vram_used_mib"),
    }


async def _unload(client: httpx.AsyncClient, model: str) -> None:
    await client.post("/api/generate", json={"model": model, "keep_alive": 0})
    await asyncio.sleep(1.5)


async def _tool_cases(client, model, cases, tools, system, required) -> list[dict]:
    out = []
    await client.post("/api/chat", json={"model": model, "messages": [], "options": {"num_ctx": 8192}})  # warm
    for i in cases:
        prompt, accepted, mention = CASES[i]
        started = time.monotonic()
        r = (await client.post("/api/chat", json={
            "model": model, "stream": False, "tools": tools,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            "options": {"num_ctx": 8192, "temperature": 0, "seed": 1},
        })).json()
        seconds = round(time.monotonic() - started, 1)
        if "error" in r:
            ok, why = False, str(r["error"])[:120]
        else:
            ok, why = score_case(r.get("message", {}), accepted, mention, required)
        out.append({"prompt": prompt, "ok": ok, "why": why, "s": seconds})
    return out


async def _follow_up(client, model, tools, system) -> dict:
    """Turn a tool result into an answer: does it use the number?"""
    started = time.monotonic()
    r = (await client.post("/api/chat", json={
        "model": model, "stream": False, "tools": tools,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": "How much GPU memory is in use right now?"},
            {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "linux.gpu_info", "arguments": {}}}]},
            {"role": "tool", "content": json.dumps(_GPU_RESULT)},
        ],
        "options": {"num_ctx": 8192, "temperature": 0, "seed": 1},
    })).json()
    text = (r.get("message") or {}).get("content", "").replace(",", "").replace(" ", "")
    ok = any(s in text for s in ("1548", "1.5GB", "1.5GiB", "1.51", "1.55"))
    return {"ok": ok, "s": round(time.monotonic() - started, 1)}


def rank(results: dict[str, dict], total_cases: int) -> tuple[list[str], dict[str, str]]:
    """(ranked models, {dropped model: reason}). Kept: enough tool cases
    passed and fully on the GPU at 8K; then fastest median answer, then
    fastest generation."""
    kept, dropped = [], {}
    for model, r in results.items():
        if r.get("error"):
            dropped[model] = r["error"]
            continue
        passed = sum(c["ok"] for c in r["tools"])
        at_8k = r["speed"][0] if r["speed"] else {}
        if passed < MIN_TOOL_SCORE * total_cases:
            dropped[model] = f"tool calls {passed}/{total_cases}"
        elif (at_8k.get("gpu_pct") or 0) < 100:
            dropped[model] = f"{at_8k.get('gpu_pct')}% on the GPU at 8K: spills to the CPU"
        else:
            kept.append(model)
    kept.sort(key=lambda m: (results[m]["median_s"], -(results[m]["speed"][0].get("gen_tps") or 0)))
    return kept, dropped


async def _benchmark(models: list[str], quick: bool = False) -> dict:
    models = [m.strip() for m in models if m.strip()][:8]
    if not models:
        raise ValueError("name at least one installed model")
    cases_all = list(QUICK_CASES if quick else range(len(CASES)))
    tools = host.model_tool_schemas()
    names = {t["function"]["name"] for t in tools}
    required = {t["function"]["name"]: t["function"].get("parameters", {}).get("required", []) for t in tools}
    # A case for a tool this Nova doesn't have would test nothing.
    cases = [i for i in cases_all if CASES[i][1] is None or set(CASES[i][1]) & names]
    try:
        system = host.service("system_prompt")()
    except RuntimeError:
        system = "You are Nova, a local AI desktop assistant."
    gpu = await asyncio.to_thread(_gpu)
    results: dict[str, dict] = {}
    async with _client(timeout=600) as client:
        installed = await _installed(client)
        version = await _ollama_version(client)
        before = [(m["name"], m.get("context_length") or 0)
                  for m in (await client.get("/api/ps")).json().get("models", [])]
        try:
            for model in models:
                if model not in installed:
                    results[model] = {"error": "not installed (pull it first)"}
                    continue
                speed = [await _speed(client, model, 8192)]
                if not quick:
                    speed.append(await _speed(client, model, 16384))
                if speed[0].get("error"):
                    results[model] = {"error": speed[0]["error"]}
                    continue
                tools_r = await _tool_cases(client, model, cases, tools, system, required)
                follow = await _follow_up(client, model, tools, system)
                await _unload(client, model)
                times = [c["s"] for c in tools_r]
                results[model] = {"speed": speed, "tools": tools_r, "follow_up": follow,
                                  "median_s": round(statistics.median(times), 1), "max_s": max(times),
                                  "size_gb": round(installed[model].get("size", 0) / 1e9, 1)}
        finally:
            # Give the chat model back, at the context it had.
            for name, ctx in before:
                try:
                    await client.post("/api/generate", json={
                        "model": name, "keep_alive": "5m", **({"options": {"num_ctx": ctx}} if ctx else {})})
                except httpx.HTTPError:
                    pass

    ranked, dropped = rank(results, len(cases))
    _save_run(results, ranked, gpu, version, quick)
    summary = {
        "ranked": ranked,
        "winner": ranked[0] if ranked else None,
        "dropped": dropped,
        "per_model": {m: _model_line(r, len(cases)) for m, r in results.items()},
        "cases": len(cases),
        "results_file": str(RESULTS_FILE),
    }
    return with_artifact(summary, _report(results, ranked, dropped, len(cases), gpu, version, quick))


def _model_line(r: dict, total: int) -> str:
    if r.get("error"):
        return r["error"]
    s8 = r["speed"][0]
    s16 = r["speed"][1] if len(r["speed"]) > 1 else {}
    return (f"tools {sum(c['ok'] for c in r['tools'])}/{total}, median {r['median_s']} s, "
            f"{s8.get('gen_tps')} tok/s, GPU {s8.get('gpu_pct')}% at 8K"
            + (f" / {s16.get('gpu_pct')}% at 16K" if s16 else "")
            + ("" if r["follow_up"]["ok"] else ", misread a tool result"))


def _save_run(results, ranked, gpu, version, quick) -> None:
    try:
        runs = json.loads(RESULTS_FILE.read_text()) if RESULTS_FILE.exists() else []
    except (OSError, ValueError):
        runs = []
    runs.append({"date": time.strftime("%Y-%m-%d %H:%M"), "ollama": version, "gpu": gpu.get("name"),
                 "vram_total_mib": gpu.get("vram_total_mib"), "quick": quick, "ranked": ranked,
                 "results": results})
    try:
        RESULTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        RESULTS_FILE.write_text(json.dumps(runs, indent=1))
    except OSError:
        pass


def _report(results, ranked, dropped, total, gpu, version, quick) -> Report:
    report = Report("Model benchmark", subtitle=f"{gpu.get('name', 'no NVIDIA GPU')} · Ollama {version}"
                    + (" · quick run" if quick else ""), kicker="Nova · model scout")
    winner = ranked[0] if ranked else None
    w = results.get(winner, {}) if winner else {}
    headroom = gpu.get("vram_total_mib", 0) - (w.get("speed", [{}])[0].get("vram_used_mib") or 0) if w else 0
    report.stats([
        Stat("Models tested", len(results), "info"),
        Stat("Winner", winner or "none", "ok" if winner else "warn"),
        Stat("Median answer", f"{w['median_s']} s" if w else "–", "info"),
        Stat("VRAM headroom", f"{headroom / 1024:.1f} GB" if w and gpu else "–", "ok" if headroom > 1024 else "warn",
             hint="at 8K context"),
    ])
    order = ranked + [m for m in results if m not in ranked]
    rows, tones = [], []
    for m in order:
        r = results[m]
        if r.get("error"):
            rows.append([m, "–", "–", "–", "–", "–", r["error"]])
            tones.append("danger")
            continue
        s8 = r["speed"][0]
        s16 = r["speed"][1] if len(r["speed"]) > 1 else {}
        tps = f"{s8.get('gen_tps')}" + (f" / {s16.get('gen_tps')}" if s16 else "")
        gpu_pct = f"{s8.get('gpu_pct')}%" + (f" / {s16.get('gpu_pct')}%" if s16 else "")
        rows.append([m, f"{sum(c['ok'] for c in r['tools'])}/{total}", f"{r['median_s']} / {r['max_s']} s",
                     tps, gpu_pct, f"{r['size_gb']} GB", "#" + str(ranked.index(m) + 1) if m in ranked
                     else dropped.get(m, "")])
        tones.append("ok" if m == winner else "" if m in ranked else "warn")
    report.section("Ranking", note=f"kept: ≥ {math.ceil(MIN_TOOL_SCORE * total)}/{total} tool calls "
                   "and fully on the GPU at 8K")
    report.table(["Model", "Tools", "Median / max", "tok/s 8K" + ("" if quick else " / 16K"),
                  "On GPU", "Size", "Rank"], rows, tones=tones, mono={0})
    findings = []
    for m in order:
        r = results[m]
        for c in r.get("tools", []):
            if not c["ok"]:
                findings.append(Finding(m, detail=f"{c['prompt']}\n→ {c['why']}", tone="warn", tag="tool call"))
        if r.get("follow_up") and not r["follow_up"]["ok"]:
            findings.append(Finding(m, detail="Answered a GPU memory question without the number from the tool.",
                                    tone="warn", tag="tool result"))
    if findings:
        report.section("Misses", note=plural(len(findings), "miss", "misses"))
        report.findings(findings[:20])
    if winner:
        s16 = w["speed"][1] if len(w["speed"]) > 1 else {}
        extra = (f" It stays fully on the GPU at 16K too ({s16.get('gen_tps')} tok/s)."
                 if s16.get("gpu_pct") == 100 else "")
        report.callout(f"{winner} answers in {w['median_s']} s on median and generates "
                       f"{w['speed'][0].get('gen_tps')} tok/s.{extra}", tone="ok", title="Recommended.")
    else:
        report.callout("No model passed both cuts. Try smaller models or a quick run with others.",
                       tone="warn", title="No winner.")
    return report


def get_tools():
    return [
        Tool(
            name="model_scout.hardware",
            description=(
                "This computer's GPU (name, VRAM total and used), RAM, CPU, Ollama version and "
                "installed models with sizes, plus the rule for what fits. Call it first when "
                "looking for the best model."
            ),
            parameters={"type": "object", "properties": {}},
            risk=RiskLevel.READ,
            implementation=_hardware,
        ),
        Tool(
            name="model_scout.lookup",
            description=(
                "Facts from ollama.com about Ollama models: for each name, its tags with download "
                "size, context, whether it fits this GPU's VRAM, whether it is installed, its "
                "capabilities (tools, thinking, vision) and the minimum Ollama version. With no "
                "names, lists current tool-capable models (works without web search)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "models": {"type": "array", "items": {"type": "string"},
                               "description": "Model names, with or without a tag (e.g. qwen3.5 or gemma4:e4b); max 10"},
                },
            },
            risk=RiskLevel.READ,
            implementation=_lookup,
        ),
        Tool(
            name="model_scout.pull",
            description=(
                "Download one Ollama model (several GB). Only after the user picked it. Refuses a "
                "model that needs a newer Ollama."
            ),
            parameters={
                "type": "object",
                "properties": {"model": {"type": "string", "description": "Model and tag, e.g. gemma4:e4b"}},
                "required": ["model"],
            },
            risk=RiskLevel.WRITE,
            implementation=_pull,
        ),
        Tool(
            name="model_scout.benchmark",
            description=(
                "Benchmark installed models on this machine with Nova's real tools: speed and GPU fit "
                "at 8K and 16K context, 12 tool-calling cases, reading a tool result. Takes about 2 "
                "minutes per model (quick: 1). Shows a ranked report by itself; returns the ranking."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "models": {"type": "array", "items": {"type": "string"}, "description": "Installed models, max 8"},
                    "quick": {"type": "boolean", "description": "8K speed and 6 tool cases only"},
                },
                "required": ["models"],
            },
            risk=RiskLevel.WRITE,
            implementation=_benchmark,
        ),
        Tool(
            name="model_scout.set_default",
            description="Make an installed model Nova's chat model. Only after the user agreed.",
            parameters={
                "type": "object",
                "properties": {"model": {"type": "string"}},
                "required": ["model"],
            },
            risk=RiskLevel.WRITE,
            implementation=_set_default,
        ),
        Tool(
            name="model_scout.remove",
            description="Delete an installed Ollama model to free disk space. Never the chat model in use.",
            parameters={
                "type": "object",
                "properties": {"model": {"type": "string"}},
                "required": ["model"],
            },
            risk=RiskLevel.DESTRUCTIVE,
            implementation=_remove,
        ),
    ]
