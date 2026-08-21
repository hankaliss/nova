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

const { app, BrowserWindow, Tray, Menu, nativeImage, ipcMain, clipboard } = require("electron");

// Surface renderer console during E2E/debug runs.
const DEBUG_RENDERER = process.env.NOVA_DEBUG_RENDERER === "1";

ipcMain.handle("nova:copy", (_e, text) => {
  clipboard.writeText(String(text));
  return true;
});
const { spawn } = require("child_process");
const path = require("path");
const fs = require("fs");

const REPO_ROOT = path.resolve(__dirname, "..");
const PYTHON = process.env.NOVA_PYTHON || "python";
let mainWindow = null;
let tray = null;
let sidecar = null;
let isQuitting = false;
let requestSeq = 0;
const pending = new Map();
let allowClose = true;

// Single instance: relaunching `nova` must focus + reload the existing
// window (the tray keeps it alive with possibly stale renderer code).
const gotSingleInstanceLock = app.requestSingleInstanceLock();
if (!gotSingleInstanceLock) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.show();
      mainWindow.focus();
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.webContents.reload();
    }
  });
}

function spawnSidecar() {
  const child = spawn(PYTHON, ["-m", "nova.rpc"], {
    env: { ...process.env, PYTHONPATH: path.join(REPO_ROOT, "src") },
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
  }
}

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
    width: 900,
    height: 700,
    minWidth: 620,
    minHeight: 460,
    title: "Nova",
    backgroundColor: "#181825",
    autoHideMenuBar: true,
    icon: path.join(__dirname, "assets", "nova-icon.png"),
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      spellcheck: false,
    },
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
      mainWindow.webContents.on("console-message", (_e, level, message) => {
        console.error(`Nova renderer[${level}]: ${message}`);
      });
    }
    if (process.env.NOVA_E2E) runEndToEnd();
    else runSampleIfRequested();
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
    {
      label: "Show / Hide",
      click: () => {
        if (mainWindow.isVisible()) mainWindow.hide();
        else {
          mainWindow.show();
          mainWindow.focus();
        }
      },
    },
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
  tray.on("click", () => {
    if (mainWindow.isVisible()) mainWindow.hide();
    else {
      mainWindow.show();
      mainWindow.focus();
    }
  });
}

// ---- IPC: renderer -> sidecar -------------------------------------------

ipcMain.handle("nova:call", (_event, method, params) => {
  return call(method, params);
});

// ---- Sample seeding + screenshots (dev only) ------------------------------

function runSampleIfRequested() {
  const sample = process.env.NOVA_SAMPLE || "";
  const shot = process.env.NOVA_SHOT || "";
  if (!sample && !shot) return;

  const emit = (event, params) =>
    mainWindow.webContents.send("nova:event", event, params);

  const shotAt = (delay) => {
    if (!shot) return;
    setTimeout(async () => {
      const image = await mainWindow.webContents.capturePage();
      fs.writeFileSync(shot, image.toPNG());
      console.error(`Nova: screenshot saved to ${shot}`);
      isQuitting = true;
      app.quit();
    }, delay);
  };

  if (!sample) {
    emit("modelsListed", [["qwen3:8b", "qwen2.5-coder:7b"]]);
    shotAt(800);
  } else if (sample === "e") {
    // HTML artifact demo: assistant replies with a complete ```html document.
    const artifactHtml = [
      "<!DOCTYPE html>",
      '<html><head><title>Pulse Dashboard</title><style>',
      "body{font-family:sans-serif;background:#101312;color:#8ae68c;",
      "display:flex;align-items:center;justify-content:center;height:100vh;margin:0}",
      ".card{border:1px solid #242b25;border-radius:8px;padding:32px;text-align:center}",
      "h1{margin:0 0 8px}p{color:#7e8f80}",
      "</style></head><body><div class=\"card\">",
      "<h1>System Pulse</h1><p>All services nominal</p>",
      "</div></body></html>",
    ].join("\n");
    emit("messageAdded", ["user", "Make me a small dashboard artifact.", false]);
    emit("messageAdded", ["assistant", "", true]);
    emit("streamingChanged", [true]);
    setTimeout(
      () =>
        emit("messageAdded", [
          "assistant",
          `Here is a small interactive artifact:\n\n\`\`\`html\n${artifactHtml}\n\`\`\`\n\nClick the badge to preview it.`,
          false,
        ]),
      400
    );
    setTimeout(() => emit("streamCompleted", []), 500);
    setTimeout(() => emit("streamingChanged", [false]), 600);
    emit("modelsListed", [["qwen3:8b", "qwen2.5-coder:7b"]]);
    // Auto-open the artifact panel and verify the sandboxed iframe mounts.
    setTimeout(async () => {
      try {
        const state = await mainWindow.webContents.executeJavaScript(
          `(() => {
            const badge = [...document.querySelectorAll("button")].find((b) => b.textContent.includes("◈"));
            if (badge) badge.click();
            return { badgeFound: !!badge };
          })()`
        );
        console.error(`Nova artifact demo: badge=${state.badgeFound}`);
        const panel = await mainWindow.webContents.executeJavaScript(
          `(() => {
            const iframe = document.querySelector("iframe[sandbox]");
            const tabs = [...document.querySelectorAll("button")].filter((b) => ["preview","source"].includes(b.textContent.trim())).length;
            return { iframe: !!iframe, srcdocLen: iframe ? iframe.getAttribute("srcdoc").length : 0, tabs };
          })()`
        );
        console.error(
          `Nova artifact demo: panel iframe=${panel.iframe} srcdoc=${panel.srcdocLen}ch tabs=${panel.tabs}`
        );
      } catch (e) {
        console.error(`Nova artifact demo failed: ${e.message}`);
      }
    }, 1200);
    shotAt(2200);
  }

  if (sample === "a") {
    emit("messageAdded", ["user", "How is the system doing right now?", false]);
    emit("messageAdded", [
      "assistant",
      "## CPU\n\n- **Model:** Intel Core i5-9600K\n- **Cores:** 6\n- **Load:** 2.5 / 1.9 / 1.6\n- **Temperature:** 62 \u00B0C\n\n## GPU\n\n- **RTX 2070** \u00B7 6.1 / 8.0 GiB VRAM \u00B7 59 \u00B0C\n\nEverything is healthy.",
      false,
    ]);
    emit("modelsListed", [["qwen3:8b", "qwen2.5-coder:7b"]]);
    shotAt(800);
  } else if (sample === "b") {
    // Wait for startup conversation restore to settle before seeding, or
    // restored messages land after ours and strand the placeholder.
    setTimeout(() => {
      emit("messageAdded", ["user", "Streaming demo \u2014 watch the caret.", false]);
      emit("messageAdded", ["assistant", "", true]);
      emit("streamingChanged", [true]);
    }, 700);
    const tokens = ["Here", " is", " a", " streaming", " response", " with", " a", " blinking", " caret"];
    tokens.forEach((t) =>
      setTimeout(() => emit("chunk", [{ content: t }]), 700 + 350 * (tokens.indexOf(t) + 1))
    );
    // During the thinking window (before the first token): rotating status
    // text with a single caret.
    setTimeout(async () => {
      try {
        const s = await mainWindow.webContents.executeJavaScript(
          `({ think: !!document.querySelector("[data-thinking]"), carets: document.querySelectorAll(".caret").length })`
        );
        console.error(`Nova stream demo: thinking text=${s.think} carets=${s.carets} (expect true/1 pre-token)`);
      } catch (e) {
        console.error(`Nova stream demo failed: ${e.message}`);
      }
    }, 950);
    setTimeout(async () => {
      try {
        const c = await mainWindow.webContents.executeJavaScript(
          `(() => {
            const t = [...document.querySelectorAll("[data-thinking]")];
            return t.map((e) => {
              let chain = [];
              let n = e;
              for (let i = 0; i < 5 && n; i++) { chain.push(n.className?.slice?.(0,60) || n.tagName); n = n.parentElement; }
              return chain;
            });
          })()`
        );
        console.error(`Nova stream demo: midstream tickers=${JSON.stringify(c)}`);
      } catch (e) {
        console.error(`Nova stream demo failed: ${e.message}`);
      }
    }, 2000);
    shotAt(2800);
  } else if (sample === "c") {
    emit("messageAdded", ["user", "Run the system metrics report.", false]);
    emit("messageAdded", ["assistant", "", true]);
    emit("streamingChanged", [true]);
    setTimeout(() => emit("toolStarted", ["system_metrics.report", "{}"]), 400);
    setTimeout(
      () => emit("toolFinished", ["system_metrics.report", true, 1240, ""]),
      1500
    );
    setTimeout(
      () =>
        emit("messageAdded", [
          "assistant",
          "The report is ready \u2014 see **System Report** below.\n\n```\nGPU: 59 \u00B0C \u00B7 75% VRAM \u00B7 29% util\n```",
          false,
        ]),
      2100
    );
    setTimeout(() => emit("streamCompleted", []), 2200);
    setTimeout(() => emit("streamingChanged", [false]), 2300);
    shotAt(2600);
  } else if (sample === "d") {
    emit("messageAdded", ["user", "Show me the GPU stats.", false]);
    emit("messageAdded", [
      "assistant",
      "## GPU\n\n- **RTX 2070** \u00B7 59 \u00B0C \u00B7 75% VRAM\n- Driver 610.57.04\n\n[Full report](https://localhost:11434)",
      false,
    ]);
    emit("metricsUpdated", [
      JSON.stringify({
        gpu: { name: "NVIDIA GeForce RTX 2070", vram_total_mib: 8192, vram_used_mib: 6187, driver: "610.57.04", temperature_c: 59, utilization_pct: 29 },
        memory: { total_bytes: 16600000000, used_bytes: 6700000000 },
        cpu: { cores: 6 },
      }),
    ]);
    emit("modelsListed", [["qwen3:8b", "qwen2.5-coder:7b"]]);
    shotAt(800);
  }
}

// ---- Real end-to-end run (dev only) -----------------------------------------

const E2E_PROMPT =
  "Generate a quick system report using the system_metrics.report tool. Use the tool result and reply briefly.";

async function runEndToEnd() {
  const shot = process.env.NOVA_SHOT || "";
  console.error("Nova E2E: starting");
  await new Promise((r) => setTimeout(r, 2000));
  try {
    const ui = await mainWindow.webContents.executeJavaScript(`(() => {
      const pill = [...document.querySelectorAll("header span")].find(
        (s) => s.textContent.includes("…") || s.textContent.trim() === "online" || s.textContent.includes("offline") || s.textContent.includes(":") || s.textContent.trim() === "models…"
      );
      const sel = document.querySelector("header select");
      const opts = sel ? [...sel.options].map((o) => o.value) : [];
      const btn = [...document.querySelectorAll("header button")].find(
        (b) => b.textContent.trim() === "Settings"
      );
      let dialog = false;
      if (btn) { btn.click(); }
      return { pill: pill ? pill.textContent.trim() : null, opts, hasSettingsBtn: !!btn };
    })()`);
    console.error(`Nova E2E: UI state — pill="${ui.pill}" models=[${ui.opts.join(", ")}] settingsBtn=${ui.hasSettingsBtn}`);
    const fit = await mainWindow.webContents.executeJavaScript(`(() => {
      const sel = document.querySelector("header select");
      if (!sel || !sel.options.length) return null;
      const cs = getComputedStyle(sel);
      const ctx = document.createElement("canvas").getContext("2d");
      ctx.font = cs.fontSize + " " + cs.fontFamily;
      const widest = Math.max(...[...sel.options].map((o) => ctx.measureText(o.text).width));
      // control must fit widest option + horizontal padding + native arrow
      return { cw: sel.clientWidth, widest: Math.round(widest), fits: sel.clientWidth >= widest + 32 };
    })()`);
    console.error(`Nova E2E: model select fit — ${JSON.stringify(fit)} (expect fits=true)`);
    // Inspector liveness: count "system" events before/after opening —
    // polling should push fresh metrics while it stays open.
    try {
      await mainWindow.webContents.executeJavaScript(
        `window.__sysCount = 0; window.nova.onEvent?.("system", () => { window.__sysCount++; }); true`
      );
      await mainWindow.webContents.executeJavaScript(`(() => {
        const btn = [...document.querySelectorAll("header button")].find((b) => b.textContent.includes("Inspector"));
        if (btn) btn.click();
      })()`);
      await new Promise((r) => setTimeout(r, 200));
      const c0 = await mainWindow.webContents.executeJavaScript(`window.__sysCount`);
      await new Promise((r) => setTimeout(r, 3600));
      const c1 = await mainWindow.webContents.executeJavaScript(`window.__sysCount`);
      console.error(
        `Nova E2E: inspector live — events@open=${c0} events@+3.6s=${c1} refreshing=${c1 > c0} (expect ≥1 and true)`
      );
    } catch (e) {
      console.error(`Nova E2E: inspector probe failed: ${e.message}`);
    }
    await new Promise((r) => setTimeout(r, 500));
    const dialogState = await mainWindow.webContents.executeJavaScript(`(() => {
      const dlg = [...document.querySelectorAll("h2")].find((h) => h.textContent.trim() === "Settings");
      // Close the dialog so later probes (e.g. the composer textarea) don't
      // accidentally match elements inside this dialog instead.
      const close = [...document.querySelectorAll("button")].find((b) => b.getAttribute("aria-label") === "Close");
      if (close) close.click();
      return { dialogOpen: !!dlg };
    })()`);
    console.error(`Nova E2E: settings dialog open=${dialogState.dialogOpen}`);
    try {
      const sw = await mainWindow.webContents.executeJavaScript(`(async () => {
        const rows = [...document.querySelectorAll("div.group.cursor-pointer")];
        const allUsers = () => [...document.querySelectorAll(".md-user")].map((b) => b.textContent.slice(0, 30));
        const rowsTitles = () => [...document.querySelectorAll("div.w-\\\\[240px\\\\] span.block.truncate.text-xs.font-bold")].map((s) => s.textContent);
        const orig = allUsers();
        const r0 = rowsTitles();
        rows[1].click();
        await new Promise((r) => setTimeout(r, 1500));
        const a = allUsers();
        const r1 = rowsTitles();
        rows[0].click();
        await new Promise((r) => setTimeout(r, 1500));
        const b2 = allUsers();
        const r2 = rowsTitles();
        return { count: rows.length, r0, r1, r2, orig, a, b: b2 };
      })()`);
      const okA = sw.a && sw.a.length > 0 && sw.a.join("|") !== sw.orig.join("|");
      const okB = sw.b.join("|") === sw.orig.join("|");
      console.error(
        `Nova E2E: switch test — count=${sw.count} preservedA=${okA} preservedB=${okB}\n  titles0=${JSON.stringify(sw.r0)}\n  titles1=${JSON.stringify(sw.r1)}\n  titles2=${JSON.stringify(sw.r2)}\n  users0=${JSON.stringify(sw.orig)}\n  users1=${JSON.stringify(sw.a)}\n  users2=${JSON.stringify(sw.b)}`
      );
    } catch (e) {
      console.error(`Nova E2E: switch test failed: ${e.message}`);
    }
    const convCount = () =>
      mainWindow.webContents.executeJavaScript(`(() => {
        return [...document.querySelectorAll("button[title*='Delete']")].length;
      })()`);
    const before = await convCount();
    let delWorked = false;
    if (before > 1) {
      // Delete a NON-active conversation with a single click on its trash
      // icon (active row gets recreated by the backend, so target row 2).
      await mainWindow.webContents.executeJavaScript(`(() => {
        const btns = [...document.querySelectorAll("button[title*='Delete']")];
        if (btns[1]) btns[1].click();
      })()`);
      await new Promise((r) => setTimeout(r, 800));
      const after = await convCount();
      delWorked = after === before - 1;
      console.error(`Nova E2E: delete button test — before=${before} after=${after} worked=${delWorked}`);
    } else {
      console.error("Nova E2E: delete button test skipped (need 2+ conversations)");
    }
    // Stop button test: drive the real UI (type + Send -> busy -> Stop button
// appears), then click Stop and verify generation halts.
// NOTE: qwen3's thinking mode means the first visible text can take 10-30s,
// but the caret/Stop button appear immediately, so we click Stop as soon as
// both are visible — no need to wait for text.
    try {
      await mainWindow.webContents.executeJavaScript(`(() => {
        const ta = document.querySelector("textarea");
        if (!ta) return;
        const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, "value").set;
        setter.call(ta, "Write a long poem, at least 250 words, about the ocean and the night sky. Do not use any tools.");
        ta.dispatchEvent(new Event("input", { bubbles: true }));
      })()`);
      await new Promise((r) => setTimeout(r, 300));
      const composer = await mainWindow.webContents.executeJavaScript(`(() => {
        const ta = document.querySelector("textarea");
        if (!ta) return null;
        return {
          h: ta.clientHeight,
          sh: ta.scrollHeight,
          overflowY: getComputedStyle(ta).overflowY,
          scrollbar: ta.scrollHeight > ta.clientHeight,
          heightCss: ta.style.height,
        };
      })()`);
      console.error(`Nova E2E: composer scrollbar test — ${JSON.stringify(composer)} (expect scrollbar=false)`);
      const userBubblesBefore = await mainWindow.webContents.executeJavaScript(`(() => {
        return [...document.querySelectorAll(".md-user")].length;
      })()`);
      const sendClicked = await mainWindow.webContents.executeJavaScript(`(() => {
        const b = document.querySelector("button[title*='Send']");
        if (!b || b.disabled) return false;
        b.click();
        return true;
      })()`);
      console.error(`Nova E2E: stop test — send clicked=${sendClicked} (user bubbles before=${userBubblesBefore})`);
      await new Promise((r) => setTimeout(r, 1000));
      const userBubblesAfter = await mainWindow.webContents.executeJavaScript(`(() => {
        return [...document.querySelectorAll(".md-user")].length;
      })()`);
      console.error(
        `Nova E2E: stop test — user bubbles before=${userBubblesBefore} after=${userBubblesAfter} (expect +1, regression: messages were duplicated)`
      );
      let grew = -1;
      for (let i = 0; i < 60; i++) {
        await new Promise((r) => setTimeout(r, 1000));
        const s = await mainWindow.webContents.executeJavaScript(`(() => {
          const b = document.querySelector(".md:not(.md-user)");
          return { text: b ? b.textContent.length : 0, streaming: !!document.querySelector(".caret"),
                   stopVisible: [...document.querySelectorAll("button")].some((x) => x.textContent.trim() === "Stop") };
        })()`);
        if (s.streaming && s.stopVisible) {
          console.error(`Nova E2E: stop test — streaming, text=${s.text}, clicking Stop`);
          await mainWindow.webContents.executeJavaScript(`(() => {
            const b = [...document.querySelectorAll("button")].find((x) => x.textContent.trim() === "Stop");
            if (b) b.click();
          })()`);
          const t0 = s.text;
          await new Promise((r) => setTimeout(r, 3000));
          const s2 = await mainWindow.webContents.executeJavaScript(`(() => {
            const b = document.querySelector(".md:not(.md-user)");
            return { text: b ? b.textContent.length : 0, streaming: !!document.querySelector(".caret") };
          })()`);
          grew = s2.text - t0;
          console.error(`Nova E2E: stop test — text grew ${grew} chars after stop, streaming=${s2.streaming} (expect small/0 and false)`);
          break;
        }
      }
      if (grew < 0) console.error("Nova E2E: stop test — never observed streaming/Stop button");
    } catch (e) {
      console.error(`Nova E2E: stop test failed: ${e.message}`);
    }
  } catch (e) {
    console.error(`Nova E2E: UI probe failed: ${e.message}`);
  }
  let prevModel = "";
  try {
    prevModel = String(await call("currentModel", []) || "");
    await call("selectModel", ["qwen3:8b"]);
    console.error("Nova E2E: using qwen3:8b (restores " + prevModel + " at end)");
  } catch (e) {
    console.error(`Nova E2E: model switch failed: ${e.message}`);
  }
  try {
    await call("sendMessage", [E2E_PROMPT]);
    console.error("Nova E2E: sendMessage accepted");
  } catch (e) {
    console.error(`Nova E2E: sendMessage failed: ${e.message}`);
  }
  const deadline = Date.now() + 120000;
  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, 1500));
    let state;
    try {
      state = await Promise.race([
        mainWindow.webContents.executeJavaScript(`(() => {
          const bubbles = [...document.querySelectorAll(".md:not(.md-user)")];
          const last = bubbles.length ? bubbles[bubbles.length - 1].textContent : "";
          return { text: last, streaming: !!document.querySelector(".caret"),
                   toolRows: [...document.querySelectorAll(".tool-row")].length };
        })()`),
        new Promise((_, rej) => setTimeout(() => rej(new Error("js timeout")), 30000)),
      ]);
    } catch (e) {
      console.error(`Nova E2E: poll failed (${e.message}), retrying`);
      continue;
    }
    if (!state.streaming && state.text.length > 0) {
      console.error(`Nova E2E: done, assistant text (${state.text.length} chars):`);
      console.error(state.text.slice(0, 500));
      try {
        const firstTitle = await mainWindow.webContents.executeJavaScript(`(() => {
          const el = document.querySelector("div.w-\\\\[240px\\\\] span.block.truncate.text-xs.font-bold");
          return el ? el.textContent : null;
        })()`);
        console.error(
          `Nova E2E: title test — title="${firstTitle}" autoTitled=${!!firstTitle && firstTitle !== "New conversation"}`
        );
      } catch (e) {
        console.error(`Nova E2E: title test failed: ${e.message}`);
      }
      // Select-to-copy: select assistant text, expect clipboard write + toast.
      try {
        const sel = await mainWindow.webContents.executeJavaScript(`(() => {
          const md = document.querySelector("div.md");
          if (!md) return { ok: false, why: "no .md" };
          const walker = document.createTreeWalker(md, NodeFilter.SHOW_TEXT);
          let node = walker.nextNode(), target = null;
          while (node) { if (node.textContent.trim().length > 20) { target = node; break; } node = walker.nextNode(); }
          if (!target) return { ok: false, why: "no long text node" };
          const t = target.textContent;
          const range = document.createRange();
          range.setStart(target, 0);
          range.setEnd(target, Math.min(40, t.length));
          const s = window.getSelection();
          s.removeAllRanges();
          s.addRange(range);
          document.querySelector("[data-chat-scroll]")?.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
          return { ok: true, expected: s.toString(), hasCopy: typeof window.nova.copyText };
        })()`);
        if (!sel.ok) throw new Error(sel.why || "selection setup failed");
        await new Promise((r) => setTimeout(r, 150));
        const clip = clipboard.readText();
        const toast = await mainWindow.webContents.executeJavaScript(
          `!!document.querySelector(".pointer-events-none.fixed.bottom-16")`
        );
        console.error(
          `Nova E2E: select-copy — clipMatch=${clip === sel.expected} toastVisible=${toast} copyApi=${sel.hasCopy} (expect true true function)`
        );
      } catch (e) {
        console.error(`Nova E2E: select-copy probe failed: ${e.message}`);
      }
      if (shot) {
        const image = await mainWindow.webContents.capturePage();
        fs.writeFileSync(shot, image.toPNG());
        console.error(`Nova E2E: screenshot saved to ${shot}`);
      }
      if (prevModel) {
        try {
          await call("selectModel", [prevModel]);
          console.error(`Nova E2E: restored model ${prevModel}`);
        } catch { /* ignore */ }
      }
      isQuitting = true;
      app.quit();
      return;
    }
    console.error(
      `Nova E2E: waiting… streaming=${state.streaming} text=${state.text.length}`
    );
  }
  console.error("Nova E2E: timed out waiting for completion");
  isQuitting = true;
  app.quit();
}

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