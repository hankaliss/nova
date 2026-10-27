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

**Status:** Electron is the only UI (`nova` launches it). The legacy PySide6/QML UI was removed on 2026-10-23 (AD-024).

**Decision history:** AD-001 (PySide6 + QML) superseded by AD-011 (Electron + React + TypeScript + Tailwind); visual identities: AD-010 "Quiet Local" → AD-013 "Aurora" → AD-014 "Phosphor" → **AD-025 "Night sky"** (current).

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
* The QML UI was kept until parity, then removed (AD-024).
* System tray + window behavior implemented natively in Electron (Linux
  tray; Windows support later).

**Electron structure (AD-011, current):**

* `electron/main.cjs` — window, tray, sidecar lifecycle, JSON-RPC bridge
  (`ipcMain.handle("nova:call")`, event forwarding), close-to-tray flow.
* `electron/dev/harness.cjs` — dev-only E2E self-test (`NOVA_E2E`), demo states
  (`NOVA_SAMPLE`) and screenshots (`NOVA_SHOT`, `capturePage()`); `require`d by
  `main.cjs` only when one of those variables is set.
* `electron/preload.cjs` — `window.nova.call/onEvent` (contextBridge).
* `electron/src/` — React + TS renderer, Tailwind v4 tokens in `styles.css`
  (**Night sky**, AD-025), marked + highlight.js (palette-based syntax theme)
  + DOMPurify. `NovaMark.tsx` is the star mark; `Meter.tsx` the shared usage
  bar. State: `hooks/useNova.ts` (chat state and events, same return shape
  as before) composes `useActivity` (activity strip), `useComposerHistory`,
  `useConversations`, `useMachine` (Ollama status, models, system metrics)
  and `useMcp`; `lib/events.ts` `eventScope()` wraps event subscriptions. Stable hooks for the dev harness: `aria-label`s on toolbar buttons,
  `data-conversation-row`/`-title`, `data-mcp-server`, `data-copy-toast`,
  `.md`/`.md-user`/`.md-streaming`/`.caret`/`.tool-row`.
* Tool cards and the activity strip show what kind of tool ran
  (`src/lib/toolKinds.tsx`, from the tool name): Web, Skill, System,
  Services, Files, Shell, Memory, MCP, Question, Skill maker.
* HTML artifacts (AD-015): `src/lib/artifacts.ts` extracts ```html docs →
  badge on the message → `ArtifactPanel.tsx` side panel rendering in a
  sandboxed `<iframe srcdoc>` (no `allow-same-origin`). Revisions are grouped
  per conversation (AD-033).
* Regenerate is offered on the latest answer only; `regenerateLast` drops the
  whole last turn (from the last user message on, tool messages included)
  from the agent context and the store (`delete_last_turn`) and asks again
  with that conversation's question.
* Startup: the sidecar replays the last conversation (`load_history`) and
  replays it again when the renderer switches to it; `conversationSwitched`
  (sent before every replay) clears the renderer's view.
* RPC sidecar (`src/nova/rpc/`, run as `python -m nova.rpc`): `NovaRpc`
  (`core.py`: init, setup, emit, quit) is assembled from one mixin per area,
  `chat`, `conversations`, `models`, `settings`, `skills`, `mcp`,
  `terminal` and `system`; `server.py` holds the stdio `Transport`, the
  frontend method aliases, `Server` dispatch and `main`; `paths.REPO_ROOT`
  anchors the bundled `skills/` and `agent.md`. Protocol: requests `{"id","method","params"}`,
  notifications `{"event","params"}`; `NovaRpc` methods are the UI-facing API.

---

# 4. LLM

Initial runtime:

**Ollama**

Default model:

**qwen3:4b** (`DEFAULT_MODEL` in `src/nova/llm/ollama.py`): 2.5 GB, fully
VRAM-resident at 8192 ctx, 28–31 tok/s on the RTX 2070. It replaced qwen3:8b
(AD-003) after the 2026-09-05 benchmark: at 8192 ctx, qwen3:8b offloads 16% to
CPU and drops to ~5 tok/s.

Model selection is exposed in the UI (`NovaRpc.listModels` / `selectModel`, a
picker showing Ollama capabilities); the chosen model is persisted in
`settings` and restored at startup. The per-model tool strategy comes from
`LLMProvider.tool_mode()` (AD-018).

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

### Context budget

`ConversationContext` stores history (capped at 100 messages) and fits it to
the model window on every request, so Ollama never silently truncates the
start of the prompt (which would drop the system prompt):

* budget = `num_ctx` (default 4096 when unset) − reply reserve (max(512,
  num_ctx/4)) − tool overhead (JSON schemas sent as `tools`, plus the text-mode
  tool instructions);
* `to_messages(max_tokens)` keeps the system prompt and the current turn
  (last user message onward), then adds older messages newest-first while
  they fit; the window always starts at a user message, so tool results are
  never sent without their call. Older messages are left out, not deleted;
* tokens are estimated with a per-model characters/token ratio measured from
  Ollama's `prompt_eval_count`, with a 10% margin (AD-038); 3 chars/token,
  which overcounts on purpose, until a model's first measurement (stored);
* each tool result is capped at ~num_ctx/4 tokens (`truncate_middle`, which
  keeps the head and tail) before it enters context.

`num_ctx` itself is chosen per model (AD-028): `"auto"` (default) fits the
largest ladder size that keeps the selected model fully on the GPU; a number
is a manual override.

Measured 2026-10-23: system prompt (`agent.md`) ≈ 2,050 tokens and 17
built-in/skill tool schemas ≈ 2,030 tokens. At 16384 (qwen3:4b on this GPU)
that leaves ≈ 8,200 tokens of history (≈ 2,000 at 8192). Enabled MCP servers
reduce it further.

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

Registered tools (besides skill and MCP tools):

| Tool | Risk | Module |
|---|---|---|
| `linux.cpu_info`, `linux.memory_info`, `linux.gpu_info`, `linux.disk_usage`, `linux.uptime`, `linux.processes`, `linux.temperatures` | READ | `tools/linux.py` |
| `services.list`, `services.status` (user + system, with journal lines) | READ | `tools/services.py` |
| `services.start`, `services.stop`, `services.restart` (user services only; session units refused, AD-036) | WRITE, not auto-approvable | `tools/services.py` |
| `web.search` (SearXNG), `web.fetch` | READ | `tools/web.py` |
| `filesystem.list`, `filesystem.read`, `filesystem.search` | READ, home directory only, secrets blocked (AD-030) | `tools/filesystem.py` |
| `shell.session_info` | READ | `tools/shell.py` |
| `shell.execute`, `shell.reset` | WRITE, not auto-approvable | `tools/shell.py` |
| `nova.presentChoices` | READ (asks the user via an inline card, AD-021) | `tools/ui.py` |
| `memory.search` | READ | `tools/memory.py` |
| `memory.remember` | WRITE, not auto-approvable | `tools/memory.py` |
| `memory.forget` | DESTRUCTIVE | `tools/memory.py` |
| `nova.skill.get` | READ | `tools/skill_maker.py` |
| `nova.skill.create`, `nova.skill.update`, `nova.skill.delete` | DESTRUCTIVE | `tools/skill_maker.py` |

MCP tools register as `mcp.<server>.<tool>`; their risk comes from per-tool
overrides and a name heuristic in `mcp/manager.py` (`get_/list_/search_` →
READ, `delete_/remove_` → DESTRUCTIVE, else WRITE). There is no per-server risk
setting (removed 2026-10-24; a legacy `"risk"` key in `mcp.json` is ignored).

Not implemented yet: filesystem tools (`filesystem.read/search`).

### Shell session (AD-022)

`shell.execute` now runs in a single long-lived interactive shell
(`nova/shell/session.py`, `ShellSession`) attached to a pseudo-terminal and
shared by the LLM and — later — the embedded terminal panel. `cwd`, exported
variables and background jobs persist between agent turns.

Implementation:

* the user's `$SHELL` (fallback `/bin/bash`) is spawned on a PTY
  (`pty.openpty`, bash `--norc`/`--noprofile`, zsh `--no-rcs`: no user rc
  files, so aliases/plugins never change what the model sees; the child calls `setsid` +
  `TIOCSCTTY` so job control works and Ctrl+C only kills the foreground
  command), with terminal echo disabled,
* a non-blocking pump task drains the master fd into a ring buffer
  (~200 KB) and notifies a changed-event + subscriber queues,
* Nova types only the bare command. Shell hooks bracket every command:
  zsh `preexec` / bash `PS0` print a start sentinel just before it runs,
  zsh `precmd` / bash `PROMPT_COMMAND` print `<marker>:<rc>|$PWD` before the
  next prompt, passing `$?` through. `run()` searches from the moment it
  types (so commands the user typed in the terminal never complete it),
  returns the bytes between the start sentinel's line and the marker as
  `{output, returncode, cwd}`, and waits for the next prompt to be drawn
  before returning (input sent mid-redraw would be echoed twice). Other
  shells get the old one-line wrapper. zsh also gets `interactive_comments`
  and an empty `PROMPT_EOL_MARK`,
* the terminal panel streams through `TerminalView`, which drops the two
  sentinel lines (chunk-safe, held-back bytes flushed after 50 ms), and its
  replay starts after the shell's setup line, so it looks like a normal
  terminal: prompt, command as typed, output,
* prompt: visible part is starship (zsh only, when a config is found at
  `$NOVA_STARSHIP_CONFIG` or `~/.config/nova/starship.toml`; only starship is
  initialised, its right prompt is disabled) or the built-in `%2~ ❯`. It ends
  in an invisible session token inside an OSC 0 title escape, used to find
  prompt boundaries after an aborted command,
* guards: trailing `&` rejected (`(cmd &)` is required), password prompts
  abort the run (with `sudo -n` guidance), timeouts send SIGINT and settle
  or hard-reset, shell death raises with a `shell.reset` hint.

Tools: `shell.execute` (WRITE / `shell.execute` grant), `shell.session_info`
(READ), `shell.reset` (WRITE). Registered in `NovaRpc.setup()`; the session
lives/dies with the sidecar (`quit()` closes it).

### Embedded terminal (AD-022)

The terminal is a **view on the same shared session**, not a second shell:

* `terminal_open`/`terminalOpen` ensures the shell is started, subscribes to
  the buffer and starts a pump task; `terminal_close`/`terminalClose` cancels
  the pump and unsubscribes (the session keeps running — `shell.execute` keeps
  working after the panel closes).
* `terminal_input` forwards raw keystrokes to the PTY master (`write_input`),
  `terminal_resize` applies TIOCSWINSZ on the master fd.
* Live output flows as `term_output` events (base64 chunks) drawn from the same
  subscriber queues as `run()` — the panel shows the exact bytes the LLM would
  see, minus the zsh/ZLE input-echo line (`_strip_input_echo`) that would reveal
  an in-flight command. Because input and output share the PTY, Ctrl+C from the
  terminal can interrupt a foreground LLM-run command (user-driven, approved).
* Rendering (Phase 2, shipped): `@xterm/xterm` + `@xterm/addon-fit` rendered in
  `electron/src/components/TerminalPanel.tsx` — a bottom dock toggled from the
  header or Ctrl+T; keystrokes go to `terminal_input`, decoded `term_output`
  chunks are drawn by xterm, and the PTY is resized to match the dock.
  `term_output` must stay out of the preload CACHEABLE set (it is a live
  stream; the panel replays the seeded tail on open).

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

**Skill maker (AD-020):** the LLM can create, update, delete and read skills via
`nova.skill.*` tools; after create/update, the skill's READ tools are
smoke-tested with empty args and failures are returned to the LLM. Create and
update load LLM-written Python into Nova's process, so they are DESTRUCTIVE and
always confirmed with the code shown (AD-023). A "Save skill" card in the chat
(for models that print code instead of calling the tool) uses the `createSkill`
RPC, which goes through the same gate.

**Locations:** bundled skills load from the repo `skills/` and are read-only to
the skill maker; skills created from chat live in `~/.local/share/nova/skills`
(`SkillLoader(user_dir=…)`, `USER_SKILLS_DIR` in `rpc/core.py`). Origin
(`bundled`/`user`) follows from the location. A user skill whose name collides
with a bundled one is ignored.

**Rendered reports (AD-039):** a skill's tool can return a finished report
next to its data (`nova.tools.report`: `Report` builder, `with_artifact`). The
report opens in the artifact panel as soon as the tool finishes; the model
only sees the data and writes a short interpretation. Bundled skills follow
this pattern: they do the gathering and aggregation in code, in one call.

Bundled skills: `system_doctor` (failed units, journal errors grouped by
source, kernel warnings, OOM kills, crashes, boot time), `git_companion`
(status, staged diff and recent log of one repo; scan of all repos under
`~/Projects`; READ only, repo config can't run code: fsmonitor off, no hooks,
no external diff) and `disk_cleaner` (`scan`: largest folders, `~/.cache`,
Ollama models, node_modules, old pacman versions as a report; `clear_cache`:
empties one folder directly under `~/.cache`, WRITE and never auto-approved,
symlinks and paths refused; package cleanup needs root so it is only
suggested). More are tracked as issues (#15 packages).

Future approaches (plugin packages, MCP) can be layered on top without breaking this format.

---

# 8. Memory

**Status:** Implemented — SQLite via `MemoryStore` (AD-007).

* `conversations` + `messages` — conversation history
* `memories` — facts the user asked Nova to keep (AD-029), with an FTS5
  index `memories_fts` (external content, trigger-synced, rebuilt on open)
* `settings` — application configuration

DB at `~/.local/share/nova/nova.db` (XDG data dir), WAL mode, stdlib sqlite3.

`NovaRpc` saves user/assistant messages explicitly and restores the last
conversation (UI + agent context) at startup. "New chat" (header, sidebar or
Ctrl+N → `clearHistory`) starts a new conversation and keeps the old one.
Deleting is done from the sidebar with a two-step confirm (first click arms
"delete?", the second deletes; it disarms after 4 s).

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
* Risk levels: READ (auto-allowed), WRITE (auto-approved or granted), DESTRUCTIVE (always requires explicit user confirmation — grants and auto-approve never apply, AD-023)
* Electron trust boundary (AD-026): only Nova's own page (main frame of the main window, sandboxed renderer) may use the preload bridge; `window.open` never creates a window (web links → system browser), the window never navigates away, artifacts open in the system browser from a private temp file, and `openPath` refuses executables and `.desktop` launchers
* `Tool.auto_approvable=False` keeps a WRITE tool out of the blanket "auto-approve WRITE tools" setting (`shell.execute`, `shell.reset`); an explicit per-tool "always allow" still works
* `nova.skill.create`/`update` are DESTRUCTIVE (they load LLM-written Python in-process); the UI `createSkill` RPC goes through the same gate and the dialog shows `tools.py`
* Async `approval_callback` — wired to the Electron permission dialog (`NovaRpc._request_permission` / `respondPermission`); "always allow" is persisted as `perm_grant_<permission>` for WRITE tools only
* Session grants via `grant()`/`revoke()` per permission string
* `ToolRegistry` consults the permission system on every execution
* Filesystem READ tools (AD-030) run without a prompt, so `PathPolicy` is their guard: paths resolve (symlinks followed) inside `$HOME` or are refused; SSH/GPG keys, keyrings, password store, browser profiles, cloud/gh credentials, Nova's own data and secret file names (`*.pem`, `*.key`, `id_rsa*`…, `.env*`, `.netrc`, `.git-credentials`…) are refused and left out of listings and searches

---

# 11. Hardware

Primary target:

NVIDIA RTX 2070 8 GB.

Model selection must be based on real benchmarks.

---

# 12. KDE

**Status:** Tray and global show/hide shortcut implemented (AD-035 in Settings, AD-034 `nova --toggle`; AD-009 was the reverted first try).

* System tray (Show/Hide, Quit) — implemented in Electron `main.cjs`.
* Global shortcut — **reverted 2026-08-19**: the `dbus-next` implementation
  crashed `kwin_wayland` (QtDBus abort while demarshaling the `keys` argument
  of `setShortcutKeys`; the compositor death took down all Wayland clients).
  Never use `dbus-next` for kglobalaccel.
* Global shortcut (AD-035): set in Nova's Settings; the sidecar registers it
  with kglobalaccel (`nova/kde/shortcut.py`, `dbus-fast`, exact signatures)
  and toggles the window on `globalShortcutPressed`. `nova --toggle`
  (AD-034) remains for a KDE-bound key that also starts Nova.

Window behavior (Electron main process):

* **Always on top:** the `always_on_top` setting is stored by the sidecar; `main.cjs` applies it from the `settings` event, which `getSettings` (startup) and every `saveSettings` emit. On native Wayland Chromium cannot keep a window on top (KWin reports `keepAbove=false`), and Electron 38+ picks native Wayland on a Wayland session, so Nova runs through XWayland: the `nova` launcher and the npm scripts pass `--ozone-platform=x11` (`NOVA_OZONE_PLATFORM=wayland` opts out). The switch must be on the real command line: set with `app.commandLine.appendSwitch` in `main.cjs` it is half-applied in Electron 44 (the GPU process segfaults, rendering falls back to software and the window is never mapped). `main.cjs` logs a warning when it finds itself on native Wayland.
* **Compact mode** (Ctrl+M or the header button): the renderer hides the sidebar, inspector and artifact panel and asks the main process (`nova:set-compact`, bridge-checked like every IPC) to shrink the window to 440×640 (minimum 360×400). Leaving it restores the previous bounds and the 620×460 minimum. Opening an artifact leaves compact mode. Remembered on this device (`localStorage` `nova.compact`); leaving it after a restart goes to the default 900×700.

Remaining (approach not yet decided):

* desktop-widget-like behavior

---

# 12.5 Settings

**Status:** Implemented (2026-08-19).

* Stored as key-value strings in the `settings` SQLite table via
  `MemoryStore.get_setting`/`set_setting`.
* `NovaRpc` owns the settings map (`getSettings`/`saveSettings` JSON
  round-trip with the renderer), applies them live: model, temperature + num_ctx
  (`Agent.options` → Ollama `options` payload), system prompt
  (`ConversationContext.system_prompt`), auto-approve WRITE tools
  (`PermissionSystem.auto_approve_writes`), close-to-tray
  (`NovaRpc.allowClose`), Ollama URL (provider created in `NovaRpc.setup`).

---

# 13. Architecture Decisions

Index (details below, in the order they were made):

| ID | Decision | Status |
|---|---|---|
| AD-001 | PySide6 + QML UI | superseded by AD-011; removed by AD-024 |
| AD-002 | PEP 621 `src` layout, `pyproject.toml` | current |
| AD-003 | Default model qwen3:8b | superseded: qwen3:4b (2026-09-05 benchmark) |
| AD-004 | Skills = `SKILL.md` + `tools.py` | current |
| AD-005 | Agent loops on Ollama tool calls | current |
| AD-006 | Web search via local SearXNG | current |
| AD-007 | SQLite memory store | current |
| AD-008 | Permission system | current, hardened by AD-023 |
| AD-009 | Global shortcut reverted (dbus-next crashed kwin) | current |
| AD-010 / 012 / 013 | Quiet Local → Local Console → Aurora UI directions | superseded |
| AD-011 | Electron + React + TS + Tailwind, Python sidecar over stdio | current |
| AD-014 | "Phosphor" visual identity | superseded by AD-025 |
| AD-015 | HTML artifacts in a sandboxed iframe side panel | current |
| AD-016 / 017 | MCP host (official SDK, stdio) + OAuth browser login | current |
| AD-018 | Model-agnostic tool calling (native / prompt / unsupported) | current |
| AD-019 | `~/.config/nova/agent.md` is the editable system prompt | current |
| AD-020 | Skill maker as tools | current (risk changed by AD-023) |
| AD-021 | `nova.presentChoices` inline choice card | current |
| AD-022 | One persistent shell shared by `shell.execute` and the terminal | current |
| AD-023 | Permission hardening | current |
| AD-024 | Legacy QML UI removed | current |
| AD-025 | "Night sky" visual identity (complete redesign) | current |
| AD-026 | Electron trust boundary (no bridged child windows, no navigation, artifacts in the system browser, safe `openPath`) | current |
| AD-027 | Default `num_ctx` 16384 | superseded by AD-028 |
| AD-028 | Automatic per-model context size (fit with real Ollama loads) | current |
| AD-029 | Persistent memory (FTS5 relevance, confirmed saves) | current |
| AD-030 | Filesystem READ tools: home only, secrets denylist, no prompt | current |
| AD-031 | Electron 44.9.3, run through XWayland (`--ozone-platform=x11` on the command line) | current |
| AD-032 | Arch PKGBUILD on repo deps (`electron44`, `python-mcp` 1.29…), `/usr/lib/nova`, `python -s` | current |
| AD-033 | Artifact revisions grouped by title; open artifact per conversation in `localStorage` | current |
| AD-034 | Global shortcut = `nova --toggle` bound in KDE Shortcuts; second instance exits at once | current |
| AD-035 | Global shortcut in Nova's Settings via kglobalaccel (`dbus-fast`, exact signatures, conflict check) | current |
| AD-036 | Process/temperature/service tools; user-service control asks every time | current |
| AD-037 | Text-mode tool calls hidden from the visible/saved reply | current |
| AD-038 | Token estimates calibrated per model from `prompt_eval_count` | current |
| AD-039 | Tools return rendered reports (report kit); shown at once, kept out of the model's context | current |

## AD-001: UI Framework

* **Date:** 2026-08-19
* **Decision:** PySide6 + QML — **superseded by AD-011, code removed by AD-024**
* **Alternatives:** Qt Widgets, Qt Widgets + QWebEngine
* **Rationale:** Native KDE integration, GPU-accelerated rendering, modern chatbot UI, streaming support
* **Consequences:** Custom markdown component needed; two-language UI layer

## AD-002: Project Structure

* **Date:** 2026-08-19
* **Decision:** PEP 621 `src` layout with `pyproject.toml`
* **Package:** `nova` (v0.0.1)
* **Source:** `src/nova/`
* **Tests:** `tests/`
* **Entry point:** `nova.__main__:main` (also `python -m nova`)
* **Dependencies:** originally PySide6 >= 6.10 (now: httpx, pydantic, mcp — AD-024)
* **Dev dependencies:** pytest >= 8
* **Rationale:** Standard Python packaging; clean separation of source and tests; setuptools package discovery

## AD-003: Default Model

* **Date:** 2026-08-19
* **Decision:** qwen3:8b — **superseded 2026-09-05 by qwen3:4b** (see §4: qwen3:8b ran at ~5 tok/s at 8192 ctx due to CPU offload)
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

## AD-018: Model-Agnostic Tool Calling

* **Date:** 2026-09-05
* **Decision:** `LLMProvider.tool_mode(model)` returns the tool strategy for each
  model: **`native`** (verified families use structured Ollama `tool_calls`),
  **`prompt`** (template advertises tools but the family is unverified or known
  sloppy — text instructions are injected into the system prompt, the `tools`
  param is still sent, and tool calls are parsed from the model's text output),
  **`unsupported`** (no tools capability — text instructions only, `tools=None`).
* **Rationale:** Nova must work with any local Ollama model. Models like
  `qwen2.5-coder:7b` advertise a "tools" template capability but emit the call
  as raw JSON inside `content` instead of structured `tool_calls`; parsing that
  text — while keeping the verified structured path for qwen3/llama3.x/mistral/
  gemma3/phi4/etc. — makes tool use model-agnostic.
* **Consequences:** The system prompt gains an auto-generated tool spec in
  text/probe modes so the model knows the available tools and the JSON call
  format; text-mode tool calls briefly appear in the streamed output (a JSON
  block) before the follow-up answer; `DEFAULT_SYSTEM_PROMPT` was made
  environment-generic (no CachyOS/KDE references) so Nova is portable.

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

## AD-014: UI Identity — "Phosphor" (supersedes AD-013 Aurora; superseded by AD-025)

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
* **Consequences:** artifacts live in the saved message text, so they come back with the conversation (verified 2026-10-26); revisions and the remembered panel are AD-033; dev sample `NOVA_SAMPLE=e` seeds an artifact for testing.

## AD-016: MCP Host Support — official SDK, stdio, write-risk default

* **Date:** 2026-08-22
* **Decision:** Nova acts as an **MCP host**: it spawns external MCP servers over the stdio transport and exposes their tools through the standard `ToolRegistry` as `mcp.<server>.<tool>`. Client implementation uses the **official Python SDK** (`mcp>=1.0`, user's choice) — `stdio_client` + `ClientSession` held open in a long-lived asyncio task per server; health-ping every 10 s; crash → exponential backoff restarts (1→30 s). Config in `~/.config/nova/mcp.json` (Claude-Desktop-compatible `{"mcpServers": …}` plus Nova fields `enabled`/`risk`/`timeout_s`; `${VAR}` expansion in env values so secrets can stay out of the file). **Default risk for third-party tools is WRITE** (user's choice) — every call goes through the existing permission/approval flow unless granted or lowered per-server.
* **Alternatives considered:** hand-rolled minimal JSON-RPC client (~250 lines, zero deps — rejected for protocol-maintenance burden); trust-servers-as-read posture (rejected: filesystem-class servers could write silently).
* **Architecture:** model never sees MCP — tools arrive via `to_schemas()` like native ones; execution flows `LLM → registry.execute → PermissionSystem → McpClient.call_tool → JSON-RPC`; text content blocks are flattened to plain strings, `isError` results become failed `ToolResult`s the LLM can reason about. Server death mid-session yields a clean failure message, not a hang.
* **UI:** dedicated header button + `McpDialog` — per-server status dot (green connected / amber pulsing starting / red failed with error / gray disabled), enable/disable switch (persists + live-applies), restart button, tool counts, add-flow seeded from templates (GitHub / Filesystem / Fetch / Custom) into an editable JSON editor, collapsible how-to guide. State arrives via the `mcp_status` event.
* **Consequences:** every enabled tool's schema rides in every prompt — keep servers minimal on 8 GB VRAM (the GitHub server's ~35 tools need its own toolset filtering); HTTP/SSE transport, exposing Nova *as* an MCP server, and per-tool filtering deferred to later phases; tests use a hand-rolled conformant fake server (`tests/fake_mcp_server.py`) so the suite needs no SDK at runtime beyond import.

## AD-017: MCP OAuth Login — browser authorization as default, challenge-aware UI

* **Date:** 2026-08-23
* **Decision:** GitHub MCP server authentication defaults to **OAuth browser login** (user's choice over PAT-first). `auto_configure_github()` emits the official docker image with the fixed loopback callback port (`-p 127.0.0.1:8085:8085` + `GITHUB_OAUTH_CALLBACK_PORT=8085`, matching the app registered in the official build); the gh-CLI-token path was removed — no plaintext tokens in `mcp.json`. PAT remains available via `${GITHUB_PAT}` expansion and takes precedence when set.
* **Auth-challenge UX:** failed MCP tool results are parsed by `nova/mcp/auth.py` (`parse_auth_challenge` — URL + optional device code, gated on authorization keywords). A detected challenge: (1) stores `{url, code}` on the client, surfaced through `mcp_status`; (2) fires the manager's `on_auth` callback once per pending challenge (deduped) → RPC emits `mcp_auth_required {server,url,code}`; (3) replaces the raw error with a short message for the LLM. A successful tool call clears pending auth.
* **Electron flow:** main.cjs captures `mcp_auth_required`, keeps only the last validated `https://` URL (10 min TTL) and **opens it in the external system browser immediately** (`shell.openExternal` — login never happens in an embedded view; the event fires once per new challenge so no repeated opens). GitHub device-login URLs are pre-filled with `?user_code=` by the parser. The renderer's *Reopen login page* button calls `window.nova.openAuthUrl()` → same validated URL — **the URL never travels from the renderer**, so there is no arbitrary-open hole. `McpAuthDialog` shows server name, code fallback, reopen button; auto-closes when `mcp_status` reports auth cleared. Header MCP button pulses amber while a login is pending; `McpDialog` cards show "login required".
* **Rationale:** the first live test relayed GitHub's raw device-code instructions through chat (isError → LLM paraphrase). OAuth is zero-setup and secret-free; parsing the challenge keeps instructions out of model context and gives one-click login.
* **Consequences:** the server keeps its token in memory only → re-authorization once per Nova launch (accepted trade-off; PAT for persistence). Port 8085 busy = fatal by design (security) — Nova surfaces it as a failed server. Detection is heuristic (keyword + URL), false positives degrade to a harmless login dialog. **Note (live-tested):** with the callback port published, github-mcp-server delivers its OAuth login request as a *successful* result — challenges are therefore detected on both error and success results, with success results requiring an unmistakably verification-style URL to avoid false logins. While a login is pending, real server calls are rate-gated (one per 30 s probe interval) — every call would restart the server's OAuth flow and invalidate the authorization URL already opened in the browser; a probe that finds the login completed simply succeeds, one that doesn't refreshes the challenge and re-opens the browser. The gate releases **instantly** when the server itself reports completion on stderr (`_StderrTap` pipes server stderr through a watcher thread that matches "authorization complete" markers — github-mcp-server logs this), or manually via "I've signed in" in the auth dialog (`mcpRetryAuth`). A changed challenge URL re-notifies and re-opens the browser.

## AD-012: Electron UI Redesign — "Local Console"

* **Date:** 2026-08-20
* **Decision:** Restructure the Electron UI: sidebar with conversation history, threaded chat with tool sub-cards, integrated composer, components extracted from the monolithic `App.tsx`; delivered in four phases.
* **Consequences:** `MemoryStore` gained conversation titles; `NovaRpc` gained conversation CRUD. The visual direction was later replaced (AD-013, then AD-014); the structure remains.

## AD-019: Editable Master Prompt

* **Date:** 2026-09-05
* **Decision:** `~/.config/nova/agent.md` is the single authoritative user system prompt, and the Settings textarea is its editor. Resolution order: user file → bundled `<repo>/agent.md` → `system_prompt` setting → built-in default. Saving writes the file (clearing it deletes the file) and applies live.
* **Rationale:** the Settings field was previously dead UI, since the file always won at startup.

## AD-020: Skill Maker

* **Date:** 2026-09-06
* **Decision:** The LLM manages skills through four tools (`nova.skill.create/update/delete/get`) backed by `SkillLoader`, which validates (name, heading, syntax, `get_tools()`, tool-name prefix) before writing anything, then registers the tools live and smoke-tests the READ ones.
* **Consequences:** risk levels were changed by AD-023 (create/update are DESTRUCTIVE).

## AD-021: Multiple-Choice Questions

* **Date:** 2026-09-06
* **Decision:** `nova.presentChoices(question, options)` is a tool that blocks on a future while the renderer shows an inline `ChoiceCard` (click, arrow keys + Enter, or 1–9); the `selectChoice` RPC resolves it with the chosen option. The user chose inline over a modal.

## AD-022: Shared Persistent Shell + Terminal

* **Date:** 2026-09-09
* **Decision:** One long-lived `$SHELL` on a PTY in the sidecar, shared by the LLM's `shell.execute` and the embedded terminal panel, so cwd/env/jobs persist across turns. LLM commands are WRITE-gated; the user's terminal may Ctrl+C an LLM command. Details in §6.

## AD-023: Permission hardening — DESTRUCTIVE is never grantable, shell never auto-approved

* **Date:** 2026-10-23
* **Decision:** (1) DESTRUCTIVE tools always ask: session/persisted grants and `auto_approve_writes` are ignored for them, and `respondPermission` never stores a grant for them (the dialog hides "Always allow"). (2) `nova.skill.create`/`nova.skill.update` are DESTRUCTIVE — they write Python that `SkillLoader` executes in-process, i.e. arbitrary code execution; the frontend `createSkill` RPC authorizes through the same tool, and the dialog shows the full `tools.py`. (3) New `Tool.auto_approvable` flag; `shell.execute`/`shell.reset` set it to `False`, so "auto-approve WRITE tools" never turns the LLM into a silent shell (an explicit per-tool "always allow" remains the user's choice).
* **Rationale:** project review found that one "always allow" bypassed DESTRUCTIVE confirmation forever, that skill creation was a WRITE-level path to arbitrary code (and the UI save card bypassed permissions entirely), and that auto-approve + `web.fetch` prompt injection gave an `LLM → arbitrary shell` path — all contrary to the security rules in CLAUDE.md.
* **Consequences:** previously stored grants for `skill_maker.create`/`update` stop applying; saving a skill always prompts with the code.

## AD-024: Remove the legacy QML UI

* **Date:** 2026-10-23
* **Decision:** Delete `src/nova/qml/`, `src/nova/ui/bridge.py` (`ChatBridge`), `src/nova/kde/tray.py` (`TrayIcon`), the `nova --qml` / `NOVA_UI=qml` entry path and their tests; drop `PySide6` and `qasync` from the dependencies.
* **Rationale:** AD-011 kept QML only until Electron reached parity, which it has (and surpassed). The duplicate UI (~3k lines) had to be kept in sync with every backend change and pulled in two heavy Linux-only dependencies.
* **Consequences:** the Python package is UI-free (sidecar only: httpx, pydantic, mcp); `nova` always launches Electron; tray lives in Electron only. Revisiting the global shortcut no longer has a Qt process to host it — use Electron `globalShortcut`.

## AD-025: UI Identity — "Night sky" (supersedes AD-014 Phosphor)

* **Date:** 2026-10-23
* **Decision:** Complete visual redesign, delegated by the user ("change completely the look… I trust you"). Method: Anthropic's `frontend-design` skill (subject → token plan → critique against generic defaults → build → screenshot critique), Vercel Web Interface Guidelines and `ui-ux-pro-max`'s checklist for the quality floor. The `ui-ux-pro-max` design-system query suggested slate + green + JetBrains Mono, i.e. essentially Phosphor, the generic default `frontend-design` warns against, so it was used only as a checklist.
* **Concept:** a *nova* is a star that suddenly flares. Calm night palette; the one bright thing is the **flare**: an eight-point star mark (`NovaMark`) on Nova's replies, large in the empty state, flaring while Nova works (respects reduced motion).
* **Tokens:** Night `#14131B` (bg), Dusk `#1C1A25` (panels), Haze `#26232F` (raised), Rule `#34303F`; Starlight `#EEEAF6` text, `#B7B1C8` / `#9A93AE` / `#8F89A4` secondary tones (all ≥ 4.5:1 on bg and panels); Flare `#F7B267` single accent, `#FFD9A8` highlight; ok `#7FDBB0`, warn `#E8D06B`, danger `#FF8A85`. Type: Bricolage Grotesque (wordmark, headings, dialog titles), Instrument Sans (UI and messages, 14–14.5px), JetBrains Mono only for code, tool names, commands and the terminal. All fonts bundled via `@fontsource-variable` (offline).
* **Layout:** full-height sidebar (brand + status, New chat, conversations grouped Today / Yesterday / Previous 7 days / Older, **machine card** with GPU name, temperature and VRAM meter: the local-first proof), slim toolbar (model picker + labelled icon buttons), centred 780px reading column (prose ≤ 72ch), user turns as right-aligned bubbles, Nova's turns unboxed with the star, floating composer. Removed: scanlines/vignette, footer statusline, all-caps labels, middle-dot meta strings, `›`/`→`/`▸` glyph chrome, mono-everywhere.
* **Consequences:** the app icon was regenerated (star on Night); xterm uses the Night palette; system metrics now poll every 10 s even with the Inspector closed (3 s when open) to feed the machine card; dev-harness probes moved to stable `aria-label`/`data-*` hooks. AD-014's signature elements (prompt-voice turns, block caret, scanlines) are retired.

## AD-026: Electron trust boundary

* **Date:** 2026-10-23
* **Decision:** Treat everything the model produces as untrusted *in the UI too*. `setWindowOpenHandler` denies every child window (http/https go to the system browser); `will-navigate`/`will-redirect` keep the window on Nova's page; IPC handlers accept calls only from the main window's main frame; the renderer is explicitly `sandbox: true` with no `<webview>`. The artifact "open" action writes the HTML to `$TMP/nova-artifacts-<uid>/<sha256>.html` (dir 0700, file 0600) and opens it in the system browser. `openPath` only opens existing absolute paths that are folders or non-executable, non-`.desktop` files.
* **Rationale:** the artifact pop-out used `window.open(blob:)`. A blob URL is same-origin with Nova's page, and Electron child windows inherit the preload, so model-written JavaScript got the full bridge, including `terminal_input` (raw keystrokes into the shared shell, no permission prompt) and `settings_save` (could enable auto-approve). The MCP login fallback opened a remote page in a bridged window the same way, and a link in an answer could navigate the app window itself.
* **Consequences:** "Open in browser" for artifacts uses the user's browser (no Nova chrome); temp artifact files accumulate in a private temp dir until the OS cleans it.

## AD-027: Default context size 16K (superseded by AD-028)

* **Date:** 2026-10-24
* **Decision:** `num_ctx` default 16384 (was 8192); the user's saved setting was updated too. Settings explains that larger values slow generation sharply once the model stops fitting.
* **Rationale:** benchmark on the RTX 2070 ([docs/benchmarks.md](docs/benchmarks.md)): qwen3:4b stays 100% on the GPU up to 16384 (6.4 GB total, 63 tok/s vs 77 at 8K); 24576 spills 17% to the CPU and drops to 5.6 tok/s. 16K quadruples the history budget (~2,000 → ~8,200 tokens).
* **Consequences:** the setting is global, so larger models (qwen3:8b already offloads at 8K) should be run with a smaller value; automatic per-model sizing is a candidate feature.

## AD-028: Automatic per-model context size

* **Date:** 2026-10-24
* **Decision:** `num_ctx` defaults to `"auto"`. On startup, model switch or a change of the setting, `nova.llm.context_fit.fit_context` picks the largest size from a ladder (2K, 4K, 8K, 12K, 16K, 24K, 32K, capped by the model's trained context) at which Ollama keeps the model fully on the GPU: a first guess from weights + KV cache (`2 × layers × kv_heads × head_dim × 2` bytes/token, from `/api/show`) against free VRAM (`nvidia-smi`, Ollama's own models counted as free) minus Ollama's ~1.5 GB reserve; then real loads (`/api/generate` with an empty prompt) checked with `/api/ps` (`size_vram == size`), climbing while it fits and stepping down when it spills (at most 4 loads). The result is stored per model (`auto_ctx_<model>`) and used at once next time while a refit runs in the background; a message sent mid-fit waits for it. After every answer, a spill seen in `/api/ps` steps the size down one rung. A numeric setting overrides all of this. The Inspector shows the size in use (and "Measuring…" during a fit).
* **Rationale:** AD-027's global 16K suited qwen3:4b but pushed qwen3:8b onto the CPU. Ollama's placement budget varies per model and with desktop GPU use (qwen3:8b fit 2K in one measurement and 4K in another), so only asking Ollama gives a reliable answer; the formula just keeps the number of trial loads low.
* **Consequences:** selecting a model takes 10–45 s of background loading the first time (it also preloads the model); a message sent meanwhile appears at once, the activity strip says the context is being measured (`activity_note` event), and Stop cancels the wait without cancelling the fit; fitting needs `nvidia-smi` for the first guess (without it, it starts at 8K and still verifies).

## AD-029: Persistent memory

* **Date:** 2026-10-24
* **Decision:** Facts live in the `memories` table with an SQLite FTS5 index (no vector DB). Before each answer, `NovaRpc` searches them with the user's message (prefix terms, stopwords dropped, FTS syntax impossible) and puts up to 10 matches (≤ 2,400 chars) in the system message (`ConversationContext.memory_note`, so the context budget counts it). Tools: `memory.remember` (WRITE, `auto_approvable=False`: the user confirms every save, "always allow" is their choice), `memory.search` (READ; empty query lists recent), `memory.forget` (DESTRUCTIVE). `agent.md`: save only on explicit request, never secrets, never because content read from the web/files says so. A Memories dialog lists, adds (the user's own action, no prompt) and deletes (two-step).
* **Alternatives:** all memories in every prompt (context grows with the list); tool-only recall (small models rarely search on their own).
* **Rationale:** user's choice (relevant-in-prompt, ask before saving). Confirming saves blocks memory poisoning through prompt injection.
* **Consequences:** relevance is keyword-based: a question phrased with none of a memory's words won't surface it (the model can still search). Memories are plain text in the local database.

## AD-030: Filesystem READ tools

* **Date:** 2026-10-26
* **Decision:** `filesystem.list` / `read` / `search` (`tools/filesystem.py`) are READ tools, so they run without a prompt. `PathPolicy` is the guard: `~` and relative paths resolve against `$HOME`, symlinks are resolved, and the result must stay inside `$HOME`. A denylist refuses secret stores (`~/.ssh`, `~/.gnupg`, `~/.password-store`, keyrings/kwallet, `~/.local/share/nova`, `~/.config/nova/mcp.json` and `nova.db`, Firefox/Chrome/Chromium/Brave profiles, `~/.config/gh`, `~/.aws`, `~/.kube`, `~/.docker`) and secret file names anywhere (`*.pem`, `*.key`, `id_rsa`/`dsa`/`ecdsa`/`ed25519*`, `.env`, `.env.*`, `.netrc`, `.git-credentials`, `.pgpass`, `.pypirc`, `.npmrc`). Denied entries are left out of listings and searches. Limits: reads return text only (NUL byte → refused), 50–2,000 lines and ≤ 100 KB per call with `next_start_line` to continue; search walks with `os.walk` (no symlinked dirs or files), skips `.git`, `node_modules`, `__pycache__`, `.venv`, `.cache` and hidden entries unless asked, matches a case-insensitive name glob and optional case-insensitive text (files ≤ 1 MB), up to 200 results / 5,000 files.
* **Alternatives:** asking for paths outside home; configured roots only; making the tools WRITE so every read prompts.
* **Rationale:** user's choice. Reading files is the most common assistant task and should not prompt; the denylist stops a prompt injection (e.g. from `web.fetch`) from reading keys without the user noticing. System files stay reachable through the approved `shell.execute`.
* **Consequences:** a denylist can miss secrets kept under unusual names. qwen3:4b read only the first window of a long file and answered from it, so `filesystem.read` takes `find` (start at the first line containing the text, 3 lines of context above; result carries `found_line`); with it the model answers section questions in one call.

## AD-031: Electron 44 on XWayland

* **Date:** 2026-10-26
* **Decision:** Electron 33 (end of life) → 44.9.3, the latest patch of the mature 44 line (45.0.0 was five days old). Nova keeps running through XWayland with `--ozone-platform=x11` on the Electron command line (launcher + npm scripts). `console-message` uses the event-object form. `npm audit fix` in the same change raised DOMPurify to 3.4.17 (a critical advisory; it sanitizes model output), Vite to 6.4.5 and source-map-js to 1.2.2.
* **Alternatives:** Electron 45.0.0; native Wayland, giving up always-on-top (or asking for a KWin window rule).
* **Rationale:** stay on a supported Electron line; always-on-top only works under XWayland (verified through KWin scripting: `keepAbove` true under X11, false under native Wayland).
* **Clipboard:** all copies go through the main process (`window.nova.copyText` → `clipboard.writeText`); the two renderer `navigator.clipboard` calls (message copy, artifact copy) were moved there. On Electron 44 under XWayland the write reaches the desktop (checked through Klipper) but Electron's own `clipboard.readText()` returns nothing; Nova never reads the clipboard, and the dev harness asks Klipper instead.
* **Consequences:** Nova is an X11 client: fractional scaling may look softer than native Wayland, and Wayland-only features (e.g. the global-shortcuts portal) need a revisit if wanted. Starting Electron directly without the flag gives native Wayland and a startup warning.

## AD-032: Arch package (PKGBUILD)

* **Date:** 2026-10-26
* **Decision:** `packaging/arch/PKGBUILD` builds the committed HEAD (`git+file://`) into a pacman package. Dependencies come from the official repos: `electron44` (the version verified in AD-031), `python`, `python-httpx`, `python-pydantic`, `python-mcp` (1.29 there; the suite and a live E2E pass on it). Layout: the wheel in site-packages; `/usr/bin/nova` is `python -s -m nova` (not the wheel's console script); the Electron app files (`main.cjs`, `preload.cjs`, `package.json`, `dist/`, `assets/`, no `dev/`) in `/usr/lib/nova/app`; `skills/` and `agent.md` in `/usr/lib/nova`; `nova.desktop` with `StartupWMClass=nova-electron`; icons in hicolor 256×256 and scalable. The renderer is built with `npm ci --ignore-scripts` (no Electron download).
* **Code changes:** `nova.rpc.paths.resources_dir()` (env → checkout → `/usr/lib/nova`); the launcher runs the installed app with the system `electron44` when it isn't in a checkout; `main.cjs` puts the repo's `src/` on `PYTHONPATH` only in a checkout and otherwise starts the sidecar with `python -s`; the dev harness loads only if present.
* **Alternatives:** a user-local install script; an AppImage.
* **Rationale:** user's choice. Everything Nova needs is in the repos, so the package is ~3.3 MB installed and pacman handles updates and removal.
* **Consequences:** packaged Nova runs on the repo `python-mcp`, which can lag PyPI (1.29 vs the 2.0 used in development). `-s` keeps a dev install in `~/.local` from shadowing the package, but also hides user-installed Python packages from the packaged sidecar. The window class stays `nova-electron` (from `package.json` `name`); renaming it to `nova` would move Electron's data into `~/.config/nova`, Nova's own config folder.

## AD-033: Artifact revisions

* **Date:** 2026-10-26
* **Decision:** `indexArtifacts(messages)` (`electron/src/lib/artifacts.ts`) numbers a conversation's artifacts as revisions of a group: the `<title>` (or fallback title) without a trailing `vN` suffix, case-insensitive. Badges show `vN` when a group has several revisions; the panel has ‹ vN of M › to step through them; when a reply finishes streaming with a new revision of the open artifact, the panel moves to it (replayed history never streams, so a revision left open stays open). The open artifact is kept by id and remembered per conversation in the renderer's `localStorage` (`nova.artifact.<conversation id>`, this device only). The panel header wraps onto a second row and drops the tab labels (container query) when narrow.
* **Alternatives:** storing artifacts in SQLite (a schema change for data the messages already hold); grouping by message order instead of title.
* **Rationale:** user's choice. Artifacts already persist inside messages, so no backend change is needed.
* **Consequences:** untitled artifacts all share the "HTML Artifact" group; a model that renames an artifact starts a new group. The renderer logic has no unit tests (no JS test runner in the project); it was verified live, including a real qwen3:4b revision.

## AD-034: Global shortcut via `nova --toggle`

* **Date:** 2026-10-26
* **Decision:** `nova --toggle` shows or hides Nova; the user binds it to a key in System Settings → Keyboard → Shortcuts (Add New → Command or Script, or the "Show or hide Nova" action of the packaged `nova.desktop`). The launcher passes `--toggle` to Electron (and skips the renderer staleness rebuild); a second Electron instance hands its argv to the running one through the single-instance lock and exits at once. The running instance's `second-instance` handler calls `toggleWindow(true)`: a visible, focused Nova hides; otherwise it is restored, shown and focused. The tray keeps hiding any visible Nova. With no Nova running, the command starts it.
* **Fix found on the way:** a second instance used to call `app.quit()` and keep running `main.cjs`, so every relaunch spawned a second sidecar (and its MCP servers) before exiting; it now calls `app.exit(0)` and returns. A toggle takes ~100 ms of Electron time.
* **Alternatives:** Electron `globalShortcut` (under XWayland, KWin only forwards keys to X11 apps per its legacy-app setting, so it may never fire); both.
* **Rationale:** user's choice. No D-Bus code in Nova (AD-009's kwin crash), works under X11 and native Wayland, and the key is managed where KDE users expect it.
* **Consequences:** one manual setup step. Verified live with KWin: hide returns focus to the previous window, show makes Nova the active window (no focus-stealing block), and a visible-but-unfocused Nova comes to the front.

## AD-035: Global shortcut set in Nova's Settings (kglobalaccel)

* **Date:** 2026-10-26
* **Decision:** Settings → General has a key-capture field (`ShortcutField.tsx`) stored as the `toggle_shortcut` setting ("" = none, text like `Ctrl+Alt+N`). The sidecar (`ShortcutMixin`, `nova/kde/shortcut.py`) registers the action `nova`/`toggle` ("Nova" / "Show or hide Nova") with kglobalaccel over the session bus using `dbus-fast` and the exact introspected signatures: `doRegister(as)`, `getComponent(s)→o`, `getGlobalShortcutsByKey(i)` (conflict check: kglobalaccel itself accepts a key another action owns), `setShortcutKeys(as, a(ai), u)` with SetPresent|NoAutoloading (Nova's setting is the source of truth), `setInactive(as)` on quit. Key text ↔ Qt key codes is validated before any call (one key plus Meta/Ctrl/Alt/Shift; typing keys need Meta, Ctrl or Alt); the encoding matches KDE's own stored codes. `globalShortcutPressed` on `/component/nova` → `toggle_window` event → `main.cjs` `toggleWindow(true)`. A taken or invalid key releases the old binding and shows the reason under the field. While the field records, the sidecar ignores Nova's own key (KDE's recorder blocks all global shortcuts instead, but that block is global and would stick if Nova crashed).
* **Safety:** kglobalaccel runs inside `kwin_wayland`, so a malformed call kills the session (AD-009). Every call was first exercised in an isolated nested KWin (`dbus-run-session -- kwin_wayland --virtual --no-lockscreen --exit-with-session <script>` with a scratch HOME/XDG_CONFIG_HOME), then against the real session.
* **Alternatives:** Electron `globalShortcut` (KWin may not forward keys to XWayland apps); keeping only the KDE-side binding (AD-034).
* **Rationale:** user's choice: the shortcut belongs in Nova's settings.
* **Consequences:** works only while Nova runs (the tray keeps it running); `nova --toggle` remains for starting Nova by key. If the key is changed in System Settings, Nova's setting wins on the next start. KDE keeps a "Nova" entry in `kglobalshortcutsrc` once registered. Non-KDE desktops show "Needs KDE Plasma's shortcut service".

## AD-036: Process, temperature and systemd service tools

* **Date:** 2026-10-26
* **Decision:** `linux.processes` (top N by CPU or resident memory, `ps` argv) and `linux.temperatures` (every `/sys/class/hwmon` sensor with its chip and label) are READ. `services.list` (user + system by default, `match` filter) and `services.status` (`systemctl show` properties plus `journalctl` lines; scope `auto` tries user, then system) are READ. `services.start/stop/restart` are WRITE with `auto_approvable=False`, act on user services only (`systemctl --user`), and refuse the desktop session's units (`plasma-*`, `kwin*`, `pipewire*`, `wireplumber*`, `dbus*`, `xdg-desktop-portal*`, `at-spi*`, `systemd-*`, `graphical-session*`, ssh/gpg agents), except restarting `plasma-plasmashell`. Unit names are validated (`^[A-Za-z0-9][A-Za-z0-9@._:\\-]*$`); everything runs as argv, never a shell.
* **Alternatives:** read-only; system-service control through polkit.
* **Rationale:** user's choice (read + user-service control). The defaults (`all`, `auto`) exist because qwen3:4b rarely passes a scope and reported the running system service `ollama` as "not running" when it looked only at user services.
* **Consequences:** system services can only be read; changing them still goes through `shell.execute` (asks every time, needs sudo).

## AD-037: Text-mode tool calls hidden from the reply

* **Date:** 2026-10-26
* **Decision:** In text mode (AD-018), `_CallHider` (`agent/agent.py`) filters each round's stream: it holds back text only while it may be a call (a fence with no language, `json`, `tool`, `tool_call` or `tool_code`, or a `{` at the start of a line), drops a complete block when every call in it names a registered tool, and releases anything else unchanged; other code fences and inline braces stream immediately, and an unclosed block is released when the round ends (or past 8,000 chars). The model's context keeps the full text including the call. Rounds are separated in front of the first visible text of the next round.
* **Rationale:** the user saw the JSON stream by and it stayed in the saved transcript (which is the concatenation of streamed tokens).
* **Consequences:** a JSON example whose `tool`/`name` is a real tool name is hidden too (it would also have been executed as a call).

## AD-038: Token estimates calibrated per model

* **Date:** 2026-10-26
* **Decision:** After each request the Agent divides the characters it sent (message contents + tool schemas) by Ollama's `prompt_eval_count` and keeps a per-model characters/token ratio (clamped to 2–6, averaged with the previous value, ignored for prompts under 200 chars). Budgeting (`estimate_tokens`, `ConversationContext.to_messages`) uses that ratio × 0.9; 3.0 until the first measurement.
* **Rationale:** the fixed 3 chars/token overestimated: qwen3:4b measured 4.28 (and `prompt_eval_count` reports the whole prompt even on a KV-cache hit, verified), so about a quarter of the history budget went unused. Ollama 0.32 has no tokenize endpoint.
* **Consequences:** the ratio is stored per model in the settings table (`cpt_<model>`, like `auto_ctx_<model>`) and loaded at startup and on model selection, so a restart keeps it; `prompt_eval_count` includes chat-template tokens, which keeps the estimate on the safe side.

## AD-039: Skills return rendered reports

* **Date:** 2026-10-27
* **Decision:** A tool result may carry an `artifact` (`{"title", "html"}`, built with `nova.tools.report.Report` and `with_artifact`). The Agent removes it from the result before the model sees it and emits it into the reply as a fenced `html` block starting with `<!-- nova:artifact -->`, so it streams to the UI at once and is saved with the reply (persistence and revisions as AD-015/033). The renderer hides that block's code (`hideToolArtifacts`), labels the badge "Report" and opens the panel when a report arrives in a streaming reply (not in replayed history, not in compact mode). History loaded into the model's context goes through `strip_artifacts`. The report kit renders one design (night-sky palette with a light variant): stat cards, bars, tables, finding cards, callouts, chips; static HTML+CSS, all text escaped, backticks neutralized so the fence can't break.
* **Alternatives:** the model writes the HTML (slow, energy-hungry, inconsistent between runs).
* **Rationale:** user's choice. A skill's value is the aggregation done in code plus the model's interpretation; typing a report token by token on a small local model is the costly part.
* **Consequences:** saved replies grow by the report size (~10–15 KB each); reports are not live (rerun the skill to refresh). The first bundled skill using it is `system_doctor` (replaces the `system_metrics` sample).

