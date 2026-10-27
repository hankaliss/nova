# Nova Development Guide

## Reference machine

Nova is developed and tested on this machine (verified 2026-10-23, updated
since). Model speeds and context sizes are in [docs/benchmarks.md](docs/benchmarks.md).

| | |
|---|---|
| OS | CachyOS, kernel 7.2.9-2-cachyos |
| Desktop | KDE Plasma 6.8.0, Wayland |
| GPU | NVIDIA RTX 2070, 8192 MiB, driver 615.78.08 |
| CPU / RAM | Intel i5-9600K (6c/6t) / 16 GB |
| Python | 3.14.8 (system) |
| Node / npm | 26.11.1 |
| Electron | 44.9.3, run through XWayland (`--ozone-platform=x11`, AD-031) |
| Ollama | 0.32.14 at `localhost:11434` |
| Models installed | `qwen3:4b`, `qwen3:8b`, `qwen2.5-coder:7b` |
| SearXNG | running at `localhost:8888` (JSON API) |

## Development Workflow

Start by checking:

```bash
git status
```

Read:

```text
CLAUDE.md
ARCHITECTURE.md
open issues: https://github.com/hankaliss/nova/issues (also on the "Nova" project board)
```

Inspect the current implementation before making changes.

## Repository layout

```text
src/nova/
  __main__.py     entry point: launches the Electron UI
  rpc/            JSON-RPC sidecar (stdio): NovaRpc = core + one mixin per
                  area (chat, conversations, models, settings, skills, mcp,
                  terminal, system); server.py = transport and dispatch
  agent/          Agent loop + ConversationContext
  llm/            LLMProvider abstraction + Ollama provider
  tools/          ToolRegistry, Tool model, linux/web/filesystem/shell/ui/
                  memory/skill_maker tools
  security/       PermissionSystem (READ / WRITE / DESTRUCTIVE)
  shell/          persistent shared shell session (PTY)
  skills/         SkillLoader
  memory/         SQLite MemoryStore
  mcp/            MCP host (config, client, manager, auth)
skills/           bundled skills (SKILL.md + tools.py)
electron/         Electron main/preload + React/TS renderer (src/)
tests/            pytest suite
agent.md          bundled system prompt (overridden by ~/.config/nova/agent.md)
packaging/        Arch PKGBUILD (packaging/arch) and the .desktop entry
```

Installed package layout (AD-032): the wheel in site-packages, the Electron
app files in `/usr/lib/nova/app` (run by the system `electron44`), bundled
`skills/` and `agent.md` in `/usr/lib/nova`. `nova.rpc.paths.resources_dir()`
picks `$NOVA_RESOURCES_DIR`, else the source checkout, else `/usr/lib/nova`;
the launcher honors `$NOVA_APP_DIR`. The package can be tested without root by
extracting the `.pkg.tar.zst` into a scratch folder and setting those two
variables plus `PYTHONPATH` (see Learnings below).

## Running from source

`nova` rebuilds the renderer if its sources changed, then launches Electron, which spawns the Python sidecar (`python -m nova.rpc`) over JSON-RPC on stdio. `cd electron && npm start` does the same without the staleness check. Both run Electron through XWayland (`--ozone-platform=x11`), because native Wayland can't keep a window on top; set `NOVA_OZONE_PLATFORM=wayland` to try native Wayland with `nova`.

Useful environment flags (dev only). If Nova is already running, add
`--user-data-dir=<tmp dir>` to the Electron command so the single-instance lock
doesn't make the second copy exit:

| Variable | Purpose |
|---|---|
| `NOVA_SAMPLE=a..e` | demo states exercising UI features (see `electron/dev/harness.cjs`) |
| `NOVA_SHOT=/path.png` | save a window screenshot, then quit (with `NOVA_SAMPLE` or `NOVA_E2E`) |
| `NOVA_E2E=1` | end-to-end self-test against a live Ollama |
| `NOVA_DEBUG_RENDERER=1` | forward renderer console to stdout |

## Tests

pytest (with pytest-asyncio) for Python, Vitest for the renderer's pure
logic (`electron/src/**/*.test.ts`, Node environment, no DOM). Tests must not
need a GPU, Ollama or network; mock external services.

```bash
python -m pytest -q                 # full suite (~85 s)
cd electron && npm test             # renderer unit tests (Vitest)
cd electron && npx tsc --noEmit     # renderer typecheck
cd electron && npm run build        # renderer build
```

CI (`.github/workflows/ci.yml`) runs the same on every push and pull
request: pytest on Python 3.12 and 3.13 (with zsh installed, so the shell
tests cover both bash and zsh), and the renderer typecheck, unit tests and
build on Node 22 (`npm ci --ignore-scripts`: no Electron binary download).

Gotcha: `shell.execute` and other WRITE/DESTRUCTIVE tools wait for a
permission response. Tests that call them through the registry must set an
approval callback, or the test hangs.

## Git

Every change goes through a pull request into `main`; `main` is protected
on GitHub (no direct pushes, no force-push, CI must pass):

1. Start from an up-to-date `main` and create a branch per issue:
   `feat/<issue>-<slug>`, `fix/<issue>-<slug>`, `docs/<slug>`.
2. Commit in small logical steps on that branch.
3. When the work is done and tested, ask the user before pushing the branch
   and opening the PR. The PR description says what changed and how it was
   verified, and ends with `Closes #<issue>`.
4. Never merge a PR without the user's explicit approval.

CI (pytest on Python 3.12 and 3.13, renderer typecheck, unit tests and
build) runs on every pull request and is required before merging.

Prefer small logical commits.

Examples:

```text
feat(llm): add ollama provider
feat(ui): add chat interface
feat(agent): add tool registry
test(llm): test streaming
```

## Multi-Session Development

At the end of every session:

1. Run relevant tests.
2. Inspect changes.
3. Update the GitHub issues: close what is done (with the commit), comment on
   what is partly done, open issues for new bugs, known issues and next tasks.
4. Document decisions in ARCHITECTURE.md and learnings below.
5. Commit when appropriate.

## Important

Never assume that the issues are more authoritative than the actual repository.

When they conflict, inspect the code and git history and correct the issues.

## Learnings

Hard-won facts about this codebase and machine. Add to them when something
cost time to find out.

- `shell.execute` is WRITE-gated. Any test or caller that drives it without answering the permission prompt hangs on the approval future. Tests drive `ShellSession.run()` directly or set an approval callback.
- Shell spawning: bash long options must come before `-i` (`--norc --noprofile -i`); `-i` on a PTY needs the child to `setsid()` + `TIOCSCTTY`; strip bracketed-paste escapes. zsh ZLE re-echoes the command despite ECHO off (`_strip_input_echo`). After SIGINT, `sudo` can swallow the interrupt → `_settle_after_abort` hard-resets.
- `term_output` is a live stream; it must not be added to the preload `CACHEABLE` events.
- Ollama keeps ~1.5–1.7 GB of free VRAM back beyond a model's reported size and spills to the CPU past that; the exact boundary moves with desktop GPU use, so context fitting verifies with real loads instead of trusting a formula. `/api/generate` with an empty prompt loads a model at the given `num_ctx` without generating.
- Shell protocol gotchas: zsh draws PROMPT_SP ("%") *before* precmd, so it would land inside run output (hence `PROMPT_EOL_MARK=''`); interactive zsh ignores `#` comments unless `interactive_comments` is set; input written before the prompt finishes drawing is echoed by the tty driver, so `run()` waits for the prompt token.
- RPC requests dispatch concurrently (`asyncio.ensure_future` per request). This is required so `stopGeneration` / `respondPermission` arrive while `sendMessage` streams.
- To run a dev Electron while Nova is already open, pass `--user-data-dir=<tmp>`; otherwise the single-instance lock makes the second copy exit. The dev copy's GitHub MCP then fails on port 8085 (already in use). This is expected.
- The real `main.cjs` can be driven live without touching the user's data: a scratch `wrapper.cjs` that `require`s `electron/main.cjs` and then a probe module (`NODE_OPTIONS=--require` doesn't work: `electron` isn't resolvable there), run as `electron wrapper.cjs --user-data-dir=<tmp>` with `HOME=<tmp>`, `PYTHONUSERBASE=$HOME/.local` (the real one, for the Python deps) and `XDG_DATA_HOME=<tmp>`. The probe sends `sendInputEvent` keys and reads `getBounds()`/`isAlwaysOnTop()`. A fake HOME keeps the real settings and DB out, but Nova seeds a GitHub MCP entry when Docker exists, so that server still starts (no login unless a tool runs).
- Electron 44 under XWayland: `clipboard.readText()` can't read back its own writes (Klipper shows the write worked: `qdbus6 org.kde.klipper /klipper org.kde.klipper.klipper.getClipboardContents`). Electron 33's installer can't extract its zip under Node 26 (only `locales/` lands); unzip `~/.cache/electron/*/electron-v33*.zip` by hand if a 33 baseline is needed again.
- `NOVA_E2E` in a fresh fake home: the model list is empty at the first probe and the conversation switch test fails (needs 2+ conversations). Both are the same on Electron 33; not regressions.
- Packaged Nova can be tested without root: `makepkg --nodeps` (with `python-installer` on `PYTHONPATH` if it isn't installed), extract the `.pkg.tar.zst` into a scratch root, put a fake `electron44` (→ `electron/node_modules/electron/dist/electron`) first on `PATH`, and run `sh <root>/usr/bin/nova` with `PYTHONPATH=<root>/usr/lib/python3.14/site-packages:<venv with the deps>`, `NOVA_APP_DIR=<root>/usr/lib/nova/app`, `NOVA_RESOURCES_DIR=<root>/usr/lib/nova` and a fake HOME. Copy `electron/dev/harness.cjs` into `<root>/usr/lib/nova/app/dev/` for `NOVA_E2E`.
- Electron's X11 window class comes from `package.json` `name` (`nova-electron`); `--class`, `app.setName()` and `CHROME_DESKTOP` don't change it.
- kglobalaccel can be tested in isolation: `HOME=<tmp> XDG_CONFIG_HOME=<tmp> dbus-run-session -- kwin_wayland --virtual --no-lockscreen --socket <name> --exit-with-session <script>`; the script talks to the nested kglobalaccel on the private bus. Without the scratch HOME/config it would rewrite the real `~/.config/kglobalshortcutsrc`. `Component.invokeShortcut(s)` fires `globalShortcutPressed` without a keyboard.
- Testing `--toggle`: launch the second instance with an async `spawn`. `spawnSync` from the first instance's main process blocks its event loop, so it can't answer the single-instance handshake and the second instance hangs.
- KWin can be asked what it really did with a window (Electron's `isAlwaysOnTop()` only reports its own flag): load a script with `qdbus6 org.kde.KWin /Scripting loadScript <file.js> <name>`, `run` it on `/Scripting/Script<id>`, then `unloadScript`; it `print`s `workspace.windowList()` fields (`caption`, `keepAbove`, `pid`) to `journalctl --user`.
- UI states can be rendered without the sidecar: a throwaway Electron main that loads `electron/dist/index.html` with `preload.cjs` and sends fake events via `win.webContents.send("nova:event", name, [payload])` using the frontend event names (`status`, `model_list`, `model_details`, `model_info`, `system`, `conversation_list`, `skill_list`, `permission`, `choices`). That's how the AD-025 screens were reviewed.
- Accessibility can be audited in the real app: install `axe-core` into a scratch folder (not the project), inject `axe.min.js` with `executeJavaScript`, then `axe.run(document)` per UI state; fake a permission prompt with `webContents.send("nova:event", "permission", [{tool, risk, permission, args}])`.
- Keyboard and focus can be checked without the sidecar: a throwaway Electron main that loads `electron/dist/index.html` with `preload.cjs`, sends `webContents.sendInputEvent` Tab/Escape keys and calls `capturePage()`. Dialogs that need backend data (Settings) won't open there; Skills does.
- The RPC stdout must flush after every message (block-buffered pipe hang).
- `nova` rebuilds the renderer when `electron/src` is newer than `dist/`. A stale `dist/` once served old event code.
- npm 12 blocks postinstall scripts; `electron` and `esbuild` are allow-listed in `electron/package.json`.
- GitHub MCP's default toolset (44 tools, ~51 KB of schemas) overflowed an 8K context and the model never called a tool. Hence `--toolsets` and per-tool risk overrides in `mcp/manager.py`.
