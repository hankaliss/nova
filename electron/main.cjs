/**
 * Nova Electron main process.
 *
 * Owns the window, tray and the Python RPC sidecar
 * (`python -m nova.rpc`) which hosts the agent stack.
 *
 * Renderer <-> main <-> sidecar:
 *   renderer: window.nova.call(method, ...params)  -> IPC invoke
 *   main:     writes JSON-RPC request to sidecar stdin, resolves on reply
 *   sidecar:  emits {"event": name, "params": [...]} notifications
 *   main:     forwards them to the renderer as 'nova:event'
 */

const { app, BrowserWindow, Tray, Menu, nativeImage, ipcMain, clipboard, shell } = require("electron");

// Nova runs through XWayland (`--ozone-platform=x11`, passed by the `nova`
// launcher and the npm scripts): Electron 38+ picks native Wayland on a
// Wayland session, where Chromium can't keep a window on top. The switch must
// be on the real command line; appendSwitch() here is too late in Electron 44
// (the GPU process crashes and the window is never shown).
// (Electron fills in the resolved platform, so check the value.)
if (app.commandLine.getSwitchValue("ozone-platform") === "wayland") {
  console.error("Nova: running on native Wayland; always-on-top won't work (start with --ozone-platform=x11)");
}

// Surface renderer console during E2E/debug runs.
const DEBUG_RENDERER = process.env.NOVA_DEBUG_RENDERER === "1";

const { spawn } = require("child_process");
const crypto = require("crypto");
const fs = require("fs");
const os = require("os");
const path = require("path");

// ---- Renderer trust boundary ------------------------------------------
// Only Nova's own page (the main frame of the main window) may use the
// bridge. Model-written content (artifacts, links in answers) must never end
// up in a window that has it.

function fromNovaPage(event) {
  return (
    mainWindow !== null &&
    !mainWindow.isDestroyed() &&
    event.sender === mainWindow.webContents &&
    event.senderFrame === mainWindow.webContents.mainFrame
  );
}

function openExternalWeb(url) {
  if (/^https?:\/\//i.test(url)) shell.openExternal(url);
}

ipcMain.handle("nova:copy", (event, text) => {
  if (!fromNovaPage(event)) return false;
  clipboard.writeText(String(text));
  return true;
});

// Artifact "open in browser": write the HTML to a private temp file and hand
// it to the default browser, where it runs with no access to Nova.
ipcMain.handle("nova:open-artifact", (event, html) => {
  if (!fromNovaPage(event) || typeof html !== "string") return false;
  const dir = path.join(os.tmpdir(), `nova-artifacts-${process.getuid?.() ?? "user"}`);
  fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
  const name = crypto.createHash("sha256").update(html).digest("hex").slice(0, 16);
  const file = path.join(dir, `${name}.html`);
  fs.writeFileSync(file, html, { mode: 0o600 });
  shell.openExternal(`file://${file}`);
  return true;
});

const REPO_ROOT = path.resolve(__dirname, "..");
// From a source checkout the sidecar imports nova from the repo's src/. The
// installed package (packaging/arch) has it in the system site-packages and
// runs Python with -s, so a dev install in ~/.local can't shadow it.
const FROM_CHECKOUT = fs.existsSync(path.join(REPO_ROOT, "src", "nova"));
const SIDECAR_ENV = FROM_CHECKOUT
  ? { ...process.env, PYTHONPATH: path.join(REPO_ROOT, "src") }
  : { ...process.env };
const SIDECAR_ARGS = FROM_CHECKOUT ? ["-m", "nova.rpc"] : ["-s", "-m", "nova.rpc"];
const PYTHON = process.env.NOVA_PYTHON || "python";
let mainWindow = null;
let tray = null;
let sidecar = null;
let isQuitting = false;
let requestSeq = 0;
const pending = new Map();
let allowClose = true;

// Show or hide the window. With `requireFocus` (the `nova --toggle` key,
// AD-034) a Nova that is open behind other windows comes to the front instead
// of hiding; the tray hides any visible Nova (clicking it may move focus to
// the panel).
function toggleWindow(requireFocus = false) {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  const shown = mainWindow.isVisible() && !mainWindow.isMinimized();
  if (shown && (!requireFocus || mainWindow.isFocused())) {
    mainWindow.hide();
    return;
  }
  if (mainWindow.isMinimized()) mainWindow.restore();
  mainWindow.show();
  mainWindow.focus();
}

// Single instance: relaunching `nova` must focus + reload the existing
// window (the tray keeps it alive with possibly stale renderer code).
// `nova --toggle` (bound to a key in KDE) only shows or hides it.
const gotSingleInstanceLock = app.requestSingleInstanceLock();
if (!gotSingleInstanceLock) {
  // The running Nova already received our argv. Stop here: app.quit() alone
  // let the rest of this file run, so every relaunch (and every toggle key
  // press) spawned a second sidecar and its MCP servers before exiting.
  app.exit(0);
  return; // CommonJS: ends this module
} else {
  app.on("second-instance", (_event, argv) => {
    if (!mainWindow || mainWindow.isDestroyed()) return;
    if (argv.includes("--toggle")) {
      toggleWindow(true);
      return;
    }
    mainWindow.show();
    mainWindow.focus();
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.webContents.reload();
  });
}

function spawnSidecar() {
  const child = spawn(PYTHON, SIDECAR_ARGS, {
    env: SIDECAR_ENV,
    stdio: ["pipe", "pipe", "inherit"],
  });
  let buffer = "";
  child.stdout.on("data", (chunk) => {
    buffer += chunk.toString("utf-8");
    let idx;
    while ((idx = buffer.indexOf("\n")) >= 0) {
      const line = buffer.slice(0, idx).trim();
      buffer = buffer.slice(idx + 1);
      if (!line) continue;
      handleLine(line);
    }
  });
  child.on("exit", (code) => {
    console.error(`nova rpc sidecar exited (code ${code})`);
    sidecar = null;
    if (!isQuitting && mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send("nova:event", "statusChanged", [
        "Backend stopped — restart Nova",
      ]);
    }
  });
  sidecar = child;
}

function handleLine(line) {
  let message;
  try {
    message = JSON.parse(line);
  } catch {
    return;
  }
  if (message.id !== undefined) {
    const entry = pending.get(message.id);
    if (!entry) return;
    pending.delete(message.id);
    if (message.error) entry.reject(new Error(message.error.message));
    else entry.resolve(message.result);
    return;
  }
  if (message.event && mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send("nova:event", message.event, message.params || []);
    if (message.event === "settings") applyWindowSettings(message.params?.[0]);
    // The global shortcut set in Settings (kglobalaccel, AD-035).
    if (message.event === "toggle_window") toggleWindow(true);
    // Capture MCP login requests so the renderer can ask the main process
    // to open the verification URL without ever supplying one itself.
    if (message.event === "mcp_auth_required") {
      try {
        const info = JSON.parse(message.params?.[0] ?? "{}");
        if (info && typeof info.url === "string" && /^https:\/\//i.test(info.url)) {
          lastAuthRequest = { url: info.url, ts: Date.now() };
          // Login happens in the external system browser — open it right
          // away (the event fires once per new challenge, so no repeats).
          shell.openExternal(info.url);
        }
      } catch {
        // malformed auth payload — ignore
      }
    }
  }
}

// Window settings live in the sidecar's store; the "settings" event (sent at
// startup and after every save) carries them. Always-on-top works through
// XWayland (Electron's default here); native Wayland has no protocol for it.
function applyWindowSettings(settings) {
  if (!settings || typeof settings !== "object") return;
  mainWindow.setAlwaysOnTop(settings.always_on_top === "true");
}

// Compact mode: a small chat-only window. The normal bounds are restored on
// the way back.
const NORMAL_MIN = [620, 460];
const NORMAL_SIZE = [900, 700];
const COMPACT_MIN = [360, 400];
const COMPACT_SIZE = [440, 640];
let normalBounds = null;

ipcMain.handle("nova:set-compact", (event, compact) => {
  if (!fromNovaPage(event)) return false;
  if (compact) {
    if (normalBounds === null) normalBounds = mainWindow.getBounds();
    if (mainWindow.isMaximized()) mainWindow.unmaximize();
    mainWindow.setMinimumSize(...COMPACT_MIN);
    mainWindow.setSize(...COMPACT_SIZE);
  } else {
    mainWindow.setMinimumSize(...NORMAL_MIN);
    // Started in compact mode (remembered by the renderer): no earlier
    // bounds to go back to, so use the default size.
    if (normalBounds !== null) mainWindow.setBounds(normalBounds);
    else mainWindow.setSize(...NORMAL_SIZE);
    normalBounds = null;
  }
  return true;
});

// Last MCP server login request captured from the sidecar (url + timestamp).
let lastAuthRequest = null;
const AUTH_URL_MAX_AGE_MS = 10 * 60 * 1000;

ipcMain.handle("nova:open-auth-url", (event) => {
  if (!fromNovaPage(event)) return false;
  const req = lastAuthRequest;
  if (!req || Date.now() - req.ts > AUTH_URL_MAX_AGE_MS) return false;
  if (!/^https:\/\//i.test(req.url)) return false;
  shell.openExternal(req.url);
  return true;
});

function call(method, params) {
  return new Promise((resolve, reject) => {
    const id = ++requestSeq;
    pending.set(id, { resolve, reject });
    if (!sidecar) {
      pending.delete(id);
      reject(new Error("Backend not running"));
      return;
    }
    sidecar.stdin.write(JSON.stringify({ id, method, params }) + "\n");
  });
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: NORMAL_SIZE[0],
    height: NORMAL_SIZE[1],
    minWidth: NORMAL_MIN[0],
    minHeight: NORMAL_MIN[1],
    title: "Nova",
    backgroundColor: "#14131b",
    autoHideMenuBar: true,
    icon: path.join(__dirname, "assets", "nova-icon.png"),
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webviewTag: false,
      spellcheck: false,
    },
  });

  // No child windows: web links go to the system browser, everything else
  // (blob:/data:/file: URLs from model content) is refused. A child window
  // would inherit the preload bridge.
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    openExternalWeb(url);
    return { action: "deny" };
  });
  // The window never navigates away from Nova's page (e.g. a clicked link
  // in an answer); web links open in the system browser instead.
  const appUrl = () => mainWindow.webContents.getURL();
  mainWindow.webContents.on("will-navigate", (event, url) => {
    if (url === appUrl()) return;
    event.preventDefault();
    openExternalWeb(url);
  });
  mainWindow.webContents.on("will-redirect", (event, url) => {
    if (url !== appUrl()) event.preventDefault();
  });

  const devUrl = process.env.NOVA_DEV_URL;
  if (devUrl) mainWindow.loadURL(devUrl);
  else mainWindow.loadFile(path.join(__dirname, "dist", "index.html"));

  mainWindow.on("close", (event) => {
    if (isQuitting) return;
    event.preventDefault();
    refreshAllowClose().then((ok) => {
      if (ok) {
        isQuitting = true;
        app.quit();
      } else {
        mainWindow.hide();
      }
    });
  });

  mainWindow.webContents.on("did-finish-load", () => {
    // Tell the renderer the sidecar is ready
    mainWindow.webContents.send("nova:event", "connected", []);
    if (DEBUG_RENDERER) {
      mainWindow.webContents.on("console-message", (event) => {
        console.error(`Nova renderer[${event.level}]: ${event.message}`);
      });
    }
    // The dev harness isn't shipped in the package (packaging/arch).
    const harness = path.join(__dirname, "dev", "harness.cjs");
    if (
      (process.env.NOVA_E2E || process.env.NOVA_SAMPLE || process.env.NOVA_SHOT) &&
      fs.existsSync(harness)
    ) {
      require(harness).runDevHarness({
        win: mainWindow,
        call,
        quit: () => {
          isQuitting = true;
          app.quit();
        },
      });
    }
  });

  return mainWindow;
}

async function refreshAllowClose() {
  try {
    allowClose = (await call("allowClose", [])) === true;
  } catch {
    allowClose = true;
  }
  return allowClose;
}

function createTray() {
  const icon = nativeImage.createFromPath(
    path.join(__dirname, "assets", "nova-icon.png")
  );
  tray = new Tray(icon);
  tray.setToolTip("Nova");
  const menu = Menu.buildFromTemplate([
    { label: "Show / Hide", click: () => toggleWindow() },
    { type: "separator" },
    {
      label: "Quit",
      click: () => {
        isQuitting = true;
        app.quit();
      },
    },
  ]);
  tray.setContextMenu(menu);
  tray.on("click", () => toggleWindow());
}

// ---- IPC: renderer -> sidecar -------------------------------------------

ipcMain.handle("nova:call", (event, method, params) => {
  if (!fromNovaPage(event)) throw new Error("Nova calls are only allowed from Nova's own page");
  return call(method, params);
});

// ---- Lifecycle -------------------------------------------------------------

app.whenReady().then(() => {
  spawnSidecar();
  createWindow();
  createTray();
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
    else {
      mainWindow.show();
      mainWindow.focus();
    }
  });
});

app.on("window-all-closed", (event) => {
  event.preventDefault();
});

app.on("before-quit", () => {
  isQuitting = true;
});

app.on("will-quit", () => {
  if (sidecar) {
    call("quit", []).catch(() => {});
    sidecar.kill();
  }
});