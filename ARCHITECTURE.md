# Nova Architecture

This document records approved architectural decisions.

Do not treat proposed architecture as approved architecture.

---

# 1. Vision

Nova is a local-first AI desktop assistant designed for:

* CachyOS
* KDE Plasma
* NVIDIA RTX 2070
* 8 GB VRAM

The assistant should provide a polished chatbot interface while being extensible through skills and tools.

---

# 2. High-Level Architecture

Target:

```text
┌──────────────────────────────┐
│             UI               │
│                              │
│ Chat / Settings / Skills     │
└──────────────┬───────────────┘
               │
               ▼
┌──────────────────────────────┐
│         Agent Core           │
│                              │
│ Context                      │
│ Planning                     │
│ Tool selection               │
│ Memory                       │
└───────┬─────────┬────────────┘
        │         │
        ▼         ▼
┌────────────┐ ┌───────────────┐
│ LLM Layer  │ │ Tool Registry │
│            │ │               │
│  Ollama    │ │ Skills / Tools│
└────────────┘ └───────┬───────┘
                       │
              ┌────────┼─────────┐
              ▼        ▼         ▼
           Linux      Web      Files
           Tools     Tools     Tools

                       │
                       ▼
                   Memory
```

---

# 3. UI

**Status:** Electron is the default UI (`nova` launches it; `nova --qml` / `NOVA_UI=qml` keeps the legacy QML window alive until removal).

**Decision history:** AD-001 (PySide6 + QML) superseded by AD-011 (Electron + React + TypeScript + Tailwind); visual identities: AD-010 "Quiet Local" → AD-013 "Aurora" → **AD-014 "Phosphor"** (current).

**Date:** 2026-08-19

**Alternatives considered (AD-011):**

1. Qt WebEngine (QML app, HTML chat pane)
2. Electron + React + TS + Tailwind
3. Tauri + React
4. Restyle the existing QML UI

**Rationale (AD-011):**

* User chose Electron — "highly designable" web frontend, cross-platform
  (incl. Windows goal), mature ecosystem (React, Tailwind, Vite), trivial
  HTML/CSS/JS design freedom vs QML constraints.
* Python agent stack stays untouched as an **RPC sidecar** over stdio
  (line-delimited JSON), preserving the LLM/Agent/Tools/Security core.
* Windows support requires a pure-Python Ollama client (no PySide6
  dependency in the sidecar) — PySide6 was Linux-only in this stack.

**Consequences:**

* Process model: Electron main ↔ renderer (contextIsolation, preload
  contextBridge) ↔ `python -m nova.rpc` sidecar.
* QML UI remains in the repo until the Electron UI reaches parity, then the
  default entry point switches.
* System tray + window behavior implemented natively in Electron (Linux
  tray; Windows support later).

**QML structure (AD-010, kept intact):**

* `qml/Theme.qml` singleton — palette (Catppuccin Mocha), pointSize type
  scale, 4-base spacing, radii, motion durations; imported via `qml/qmldir`.
* `qml/main.qml` — single window: header (brand, actions, model combo),
  chat card (bubbles + empty state + tool timeline), inspector (default
  closed), error banner, status strip, input row, dialogs.
* Chat pipeline: `messageAdded` / `streamUpdated` / `streamCompleted`.
* Tool activity: `Agent.tool_callback` → `toolStarted` / `toolFinished`
  → per-bubble timeline rows.
* Permissions: `permissionRequested` → dialog → `respondPermission`
  (persisted per-tool grant `perm_grant_<permission>`).
* Metrics: `refreshMetrics` → `metricsUpdated` (GPU/RAM/CPU, 3 s while
  window visible).
* Dev screenshot tooling: `NOVA_SAMPLE=a|b|c|d|e`, `NOVA_SHOT=/path.png`
  (env-gated; `e` seeds an HTML artifact and auto-verifies the panel).

**Electron structure (AD-011, current):**

* `electron/main.cjs` — window, tray, sidecar lifecycle, JSON-RPC bridge
  (`ipcMain.handle("nova:call")`, event forwarding), close-to-tray flow,
  E2E probes + dev tooling (`NOVA_SAMPLE`/`NOVA_SHOT` with `capturePage()`).
* `electron/preload.cjs` — `window.nova.call/onEvent` (contextBridge).
* `electron/src/` — React + TS renderer, Tailwind v4 tokens (**Phosphor**,
  AD-014: charcoal-green ground, phosphor text, JetBrains Mono, prompt-voice
  turns, block caret, scanlines), marked + highlight.js + DOMPurify.
* HTML artifacts (AD-015): `src/lib/artifacts.ts` extracts ```html docs →
  badge on the message → `ArtifactPanel.tsx` side panel rendering in a
  sandboxed `<iframe srcdoc>` (no `allow-same-origin`).
* RPC protocol (`src/nova/rpc.py`): requests `{"id","method","params"}`,
  notifications `{"event","params"}`; methods mirror the ChatBridge surface.

---

# 4. LLM

Initial runtime:

**Ollama**

Default model:

**qwen3:8b** (Q4_K_M, 8.2B parameters, 5439 MiB VRAM, 56.5 tok/s on RTX 2070)

Model selection is exposed in the UI (`ChatBridge.listModels` / `selectModel`);
the chosen model is persisted in `settings` and restored at startup.

The application should communicate through an abstraction rather than coupling the agent directly to Ollama.

Potential future providers:

* Ollama
* llama.cpp
* OpenAI-compatible local servers
* remote providers

---

# 5. Agent

The agent is responsible for:

* receiving user input
* constructing context
* deciding whether tools are needed
* calling tools
* processing tool results
* generating the final response
* managing conversation context

The agent should not directly access the operating system.

---

# 6. Tools

Tools are explicitly registered.

Each tool should expose:

* name
* description
* schema
* implementation
* risk level
* permission requirements

---

# 7. Skills

**Status:** Decided — Markdown + Python format (AD-004).

```text
skills/<name>/
├── SKILL.md    # name and description
└── tools.py    # get_tools() returning list[Tool]
```

`SkillLoader` discovers skill directories and registers their tools into the
`ToolRegistry`. Skills are manageable at runtime: the UI dialog lists name,
description and tools (risk badges) with enable/disable switches; enabled
state persists in settings (`skill_enabled_<name>`) and disabled skills are
not registered at startup.

Future approaches (plugin packages, MCP) can be layered on top without breaking this format.

---

# 8. Memory

**Status:** Implemented — SQLite via `MemoryStore` (AD-007).

* `conversations` + `messages` — conversation history
* `memories` — persistent user memories
* `settings` — application configuration

DB at `~/.local/share/nova/nova.db` (XDG data dir), WAL mode, stdlib sqlite3.

`ChatBridge` saves user/assistant messages explicitly and restores the last
conversation (UI + agent context) at startup. "Clear History" deletes the
stored conversation.

Vector databases are not used (no demonstrated need).

---

# 9. Web

Web access is implemented through explicit tools:

```text
web.search(query, num_results)  # local SearXNG JSON API
web.fetch(url, max_chars)       # fetch page text, stripped and truncated
```

**Status:** Decided — SearXNG (AD-006).

Unrestricted browser access is not used. SearXNG must run locally on `localhost:8888`.

---

# 10. Security

LLM actions are untrusted.

Execution path:

```text
LLM
 ↓
Agent
 ↓
Tool Registry
 ↓
Permission System
 ↓
Tool
```

**Status:** Implemented (AD-008).

* `PermissionSystem` — policy validation + approval flow
* `PermissionDenied` — raised when a tool request is not permitted
* Risk levels: READ (auto-allowed), WRITE (auto-approved or session-granted), DESTRUCTIVE (always requires explicit user confirmation)
* Async `approval_callback` — wired to the QML permission dialog (`ChatBridge.request_permission` / `respondPermission`)
* Session grants via `grant()`/`revoke()` per permission string
* `ToolRegistry` consults the permission system on every execution

---

# 11. Hardware

Primary target:

NVIDIA RTX 2070 8 GB.

Model selection must be based on real benchmarks.

---

# 12. KDE

**Status:** Tray implemented; global shortcut reverted (AD-009).

* `TrayIcon` — system tray with Show/Hide and Quit, drawn icon (no theme dependency).
* Global shortcut — **reverted 2026-08-19**: the `dbus-next` implementation
  crashed `kwin_wayland` (QtDBus abort while demarshaling the `keys` argument
  of `setShortcutKeys`; the compositor death took down all Wayland clients).
  When revisited, the shortcut must use PySide6 `QDBusArgument` (Qt-side
  marshaling), never `dbus-next`.

Remaining:

* global shortcut (QtDBus-based)
* always-on-top
* compact mode
* desktop-widget-like behavior

Implementation approach not yet decided for the remaining items.

---

# 12.5 Settings

**Status:** Implemented (2026-08-19).

* Stored as key-value strings in the `settings` SQLite table via
  `MemoryStore.get_setting`/`set_setting`.
* `ChatBridge` owns the settings map (`getSettings`/`saveSettings` JSON
  round-trip with QML), applies them live: model, temperature + num_ctx
  (`Agent.options` → Ollama `options` payload), system prompt
  (`ConversationContext.system_prompt`), auto-approve WRITE tools
  (`PermissionSystem.auto_approve_writes`), close-to-tray
  (`ChatBridge.allowClose`), always-on-top (window flags in QML), Ollama URL
  (provider recreated at startup in `__main__`).

---

# 13. Architecture Decisions

## AD-001: UI Framework

* **Date:** 2026-08-19
* **Decision:** PySide6 + QML
* **Alternatives:** Qt Widgets, Qt Widgets + QWebEngine
* **Rationale:** Native KDE integration, GPU-accelerated rendering, modern chatbot UI, streaming support
* **Consequences:** Custom markdown component needed; two-language UI layer

## AD-002: Project Structure

* **Date:** 2026-08-19
* **Decision:** PEP 621 `src` layout with `pyproject.toml`
* **Package:** `nova` (v0.0.1)
* **Source:** `src/nova/`
* **QML:** `src/nova/qml/`
* **Tests:** `tests/`
* **Entry point:** `nova.__main__:main` (also `python -m nova`)
* **Dependencies:** PySide6 >= 6.10
* **Dev dependencies:** pytest >= 8
* **Rationale:** Standard Python packaging; clean separation of source and tests; setuptools package discovery

## AD-003: Default Model

* **Date:** 2026-08-19
* **Decision:** qwen3:8b
* **Alternatives:** qwen2.5-coder:7b
* **Rationale:** Faster time-to-first-token (4593 ms vs 6814 ms), "thinking" capability, 40K context. VRAM usage (5439 MiB) fits within 8 GB.
* **Consequences:** 790 MiB more VRAM than qwen2.5-coder:7b; slightly slower generation (56.5 vs 62.3 tok/s)

## AD-004: Skill Architecture

* **Date:** 2026-08-19
* **Decision:** Markdown + Python files (skills/<name>/SKILL.md + tools.py with get_tools())
* **Alternatives:** Python plugin packages, MCP-based skills
* **Rationale:** Simple, human-readable, reuses Tool model and ToolRegistry; plugin packages / MCP can be layered later
* **Consequences:** Skill code runs with app privileges — mitigated by READ-only tools and permission gate

## AD-005: Agent Tool Calling

* **Date:** 2026-08-19
* **Decision:** Agent loops on Ollama function calls — request → detect tool_calls → registry.execute() → append tool result → re-request
* **Rationale:** qwen3:8b supports the tools capability; matches required security path LLM → Agent → ToolRegistry → Tool
* **Consequences:** `max_tool_iterations` guard (default 10) prevents infinite tool loops; intermediate tool responses are not shown in the UI

## AD-006: Web Search Provider

* **Date:** 2026-08-19
* **Decision:** SearXNG (self-hosted, localhost:8888 JSON API)
* **Alternatives:** Brave Search API, DuckDuckGo
* **Rationale:** Local-first and privacy-focused; no API keys or cost; JSON format supported
* **Consequences:** Requires SearXNG to be installed and running; `web.search` reports a clear error when it is not reachable

## AD-007: Memory

* **Date:** 2026-08-19
* **Decision:** SQLite via `MemoryStore` (stdlib sqlite3, WAL, XDG data dir)
* **Alternatives:** Vector database (rejected — no demonstrated need), JSON files
* **Rationale:** Simple, robust, standard; separates conversations, memories, settings
* **Consequences:** No vector search; conversation history is saved explicitly, not automatically

## AD-008: Permission System

* **Date:** 2026-08-19
* **Decision:** `PermissionSystem` in `src/nova/security/`; READ auto-allowed, WRITE auto-approved or session-granted, DESTRUCTIVE always requires explicit user confirmation
* **Alternatives:** Static allowlist config, hard deny of all non-READ tools
* **Rationale:** Runtime validation keeps the LLM↔tool boundary controlled; approval callback lets the UI present confirmation dialogs without blocking the async loop
* **Consequences:** Without an approval callback DESTRUCTIVE/WRITE requests are denied by default; session grants give one-shot approvals

## AD-009: Global Shortcut Reverted

* **Date:** 2026-08-19
* **Decision:** Remove the `dbus-next`-based kglobalaccel global shortcut; keep tray integration only. Revisit the shortcut using PySide6 QtDBus marshaling (`QDBusArgument`).
* **Rationale:** The dbus-next message for `setShortcutKeys` (`asa(ai)u`) carried malformed `a(ai)` data that made kwin's QtDBus abort (signal 6), crashing the compositor and every Wayland client (Konsole, Firefox). Qt-side marshaling matches the receiver's demarshaling and fails with error replies instead of crashing.
* **Consequences:** No global shortcut until rewritten; `dbus-next` dependency removed.

## AD-010: UI/UX Direction — "Quiet Local"

* **Date:** 2026-08-19
* **Decision:** Nova UI follows "Quiet Local" — calm, local-first-transparent, KDE-native-minimal; Catppuccin Mocha formalized into design tokens; DPI-aware typography; flat unified components.
* **Rationale:** From a 15-criterion audit of the initial UI (contrast, spacing, hierarchy, affordances, motion); all findings + tiers + don't-change list recorded in the session notes.
* **Consequences:** Token system (`Theme.qml` singleton / CSS variables), component library, reduced motion setting, permission dialog upgrade, empty state, status strip, inspector.

## AD-011: UI Framework Migration — Electron

* **Date:** 2026-08-19
* **Decision:** Replace the QML frontend with **Electron + React + TypeScript + Tailwind CSS** (user choice). Python agent stack stays as an RPC sidecar (`python -m nova.rpc`, JSON-RPC over stdio). QML UI kept until Electron parity.
* **Alternatives:** Qt WebEngine, Tauri, restyle QML.
* **Rationale:** User preference for a highly designable web frontend; cross-platform goal incl. Windows; React/Tailwind ecosystem; web tech for markdown/highlighting is native.
* **Consequences:** Two-process app (Electron + Python sidecar); PySide6/QML no longer the UI layer; Windows later needs only the pure-Python backend; `src/nova/rpc.py` becomes the UI-facing API.

## AD-013: UI Identity — "Aurora" (replaces AD-010's Quiet Local look)

* **Date:** 2026-08-20
* **Decision:** Full new visual direction chosen via Anthropic's `frontend-design` skill (installed at `~/.config/opencode/skills/frontend-design/`). **Aurora**: deep blue-charcoal (`#0B0D14` bg, `#131724` surface, `#262E44` hairlines), cold near-white text (`#E4E9F7`), **aurora gradient** (indigo `#7C8CFF` → teal `#4FD6C4`) confined to exactly three signature spots — the NOVA wordmark, the send button + streaming caret, the empty-state mark; everything else disciplined dark.
* **Type roles:** display **Space Grotesk** (wordmark, dialog titles, streaming indicator, used with restraint), body **Inter**, utility **JetBrains Mono** (model pill, timestamps, inspector data, footer hints). All bundled via `@fontsource` (local-first, offline; fixes the previous un-bundled Inter silent fallback).
* **Method:** skill-mandated brainstorm (token system: color/type/layout/signature) → critique pass rejecting the three AI-slop defaults (cream+serif+terracotta, acid-green-on-black, broadsheet hairlines) → build.
* **Rationale:** grounded in the subject's world (KDE Plasma auroras; "nova" = exploding star — charged particles meeting your machine, the local-AI metaphor); keeps Nova's calm DNA by spending the boldness in one place (gradient) and staying quiet elsewhere.
* **Consequences:** tokens in `electron/src/styles.css` `@theme`; `aurora-text`/`aurora-bg` utilities; `reduce-motion` still respected; E2E-referenced selectors/labels kept stable (pill shows model id, Stop/Settings/Skills labels, `.caret`, `.md` classes). AD-010's functional decisions (tokens, components, reduced motion) remain; its Catppuccin palette is retired.

## AD-014: UI Identity — "Phosphor" (supersedes AD-013 Aurora)

* **Date:** 2026-08-21
* **Decision:** Full visual re-skin chosen from 9 rendered mockup directions (`/tmp/opencode/nova-previews/`, user picked **Phosphor** — terminal heritage). Charcoal-green ground (`#101312` bg, `#161A17`/`#1B201C` surfaces, `#242B25` lines), phosphor-green text (`#8AE68C`), bright lime for user turns/emphasis (`#D9F99D`), amber warn (`#E8C96F`). JetBrains Mono is the dominant face (Inter kept as fallback); Space Grotesk dropped.
* **Signature moves:** no chat bubbles — prompt voice (`you ›` / `nova ›` prefixes, user text bright, assistant phosphor); blinking block-cursor caret while streaming; faint scanline + vignette overlay on the whole window; statusline header/footer; dashed "+ new chat" sidebar button with `▸` active marker; tool activity as terminal tree rows (`├─ name ✓ ok 142ms`); empty state = glowing `nova▊` wordmark + `$ command` starter list.
* **Rationale:** user's explicit pick among mockups; matches Nova's local-terminal soul and the KDE tiling aesthetic; mono-everything removes font-loading ambiguity.
* **Consequences:** all tokens replaced in `styles.css` `@theme`; `.glow-text` replaces aurora utilities; sharp corners (rounded-none) on interactive chrome; E2E selectors/labels preserved (`.md`, `.md-user`, `.caret`, `.tool-row` now actually rendered, Settings/Skills/Stop/Send labels, `w-[240px]` sidebar).

## AD-015: HTML Artifacts — sandboxed side-panel preview

* **Date:** 2026-08-21
* **Decision:** Model can emit complete HTML documents in ```html fenced blocks; the renderer auto-detects them (`electron/src/lib/artifacts.ts`) and shows a clickable artifact badge under the message. Clicking opens a **right-side panel** (`ArtifactPanel.tsx`, 480px) with preview/source tabs, copy, reload, open-in-browser. Rendering happens in a sandboxed `<iframe srcdoc>` with `sandbox="allow-scripts allow-modals allow-forms allow-popups"` — **no** `allow-same-origin`, so artifact JS runs in an opaque origin with zero access to Electron APIs, the parent DOM, or local files. System prompt (`src/nova/agent/context.py`) instructs the model to emit self-contained single-file HTML when asked for pages/games/dashboards.
* **Alternatives considered:** inline expandable card in chat; modal overlay; a save-artifact tool the model must call.
* **Rationale:** side panel matches Claude/Copilot artifact UX (user choice); auto-detection needs no tool-calling overhead and works mid-stream later; iframe sandbox is the standard isolation boundary for untrusted LLM-generated HTML.
* **Consequences:** artifacts are ephemeral UI state (cleared on conversation switch/clear); no persistence or versioning yet; dev sample `NOVA_SAMPLE=e` seeds an artifact for testing.
