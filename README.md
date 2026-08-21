# Nova

Nova is a local-first AI desktop assistant for CachyOS and KDE Plasma.

It runs local LLMs through Ollama and provides a polished chatbot interface with extensible skills, tools, memory and controlled web access. Nothing leaves your machine.

![stack](https://img.shields.io/badge/Python-3.12%2B-blue) ![electron](https://img.shields.io/badge/UI-Electron%20%2B%20React-green)

## Features

- Local LLM chat via Ollama (streaming, stop, regenerate)
- Model picker populated dynamically from your installed Ollama models
- Conversation history with auto-titling (SQLite)
- Tool system with permission levels (read / write / destructive) and approval prompts
- Skills (independently toggleable instruction + tool packages)
- HTML artifacts rendered in a sandboxed side panel
- Live system inspector: GPU / VRAM / RAM / CPU
- Select-to-copy with clipboard toast
- Phosphor terminal-inspired dark UI

## Prerequisites

- Linux (developed on CachyOS / KDE Plasma, Wayland)
- Python 3.12+
- Node.js 18+ and npm
- [Ollama](https://ollama.com) running at `localhost:11434` with at least one model pulled, e.g.

```bash
ollama pull qwen3:8b
```

## Install

```bash
git clone <your-repo-url> nova
cd nova

# Python sidecar + backend
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Electron UI
cd electron
npm install
```

## Run

With the virtualenv active:

```bash
cd electron
npm start
```

This builds the renderer, launches Electron, which spawns the Python sidecar (`python -m nova.rpc`) over JSON-RPC on stdio.

Useful environment flags:

| Variable | Purpose |
|---|---|
| `NOVA_SAMPLE=a..e` | demo scripts exercising UI features (see `electron/main.cjs`) |
| `NOVA_E2E=1` | end-to-end self-test against a live Ollama |
| `NOVA_DEBUG_RENDERER=1` | forward renderer console to stdout |

## Tests

```bash
python -m pytest -q          # 201 unit/integration tests, no GPU required
```

## Configuration

- Settings dialog in-app (model, temperature, context size, system prompt, Ollama URL)
- Data lives in `~/.local/share/nova/` (SQLite), config in `~/.config/nova/`
- `agent.md` at the repo root is the fallback system prompt; override it with `~/.config/nova/agent.md`

## Documentation

- `ARCHITECTURE.md` — components, data flow, approved decisions
- `DEVELOPMENT.md` — workflow conventions

## Status

Early development. See the feature list above for what works today.
