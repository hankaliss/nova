const { contextBridge, ipcRenderer } = require("electron");

// State events: only the latest value matters; buffer it so subscribers that
// register after the event fired (renderer mount races with sidecar startup
// events) still receive the current state.
const CACHEABLE = new Set([
  "connected",
  "status",
  "system",
  "skill_list",
  "model_list",
  "model_info",
  "settings",
  "response",
  "conversation_list",
  "conversationSwitched",
  "conversationDeleted",
  "shortcut_status",
]);

const subscribers = new Map();
const lastValue = new Map();

ipcRenderer.on("nova:event", (_ev, name, params) => {
  if (CACHEABLE.has(name)) lastValue.set(name, params || []);
  const list = subscribers.get(name);
  if (list) for (const cb of list) cb(params || []);
});

contextBridge.exposeInMainWorld("nova", {
  call: (method, params) => ipcRenderer.invoke("nova:call", method, params || {}),
  copyText: (text) => ipcRenderer.invoke("nova:copy", String(text)),
  // Opens the verification URL of the last captured MCP login request.
  // The URL itself lives in the main process only — the renderer can
  // trigger the open but never choose which URL gets opened.
  openAuthUrl: () => ipcRenderer.invoke("nova:open-auth-url"),
  // Artifact HTML opens in the system browser (never in a Nova window).
  openArtifact: (html) => ipcRenderer.invoke("nova:open-artifact", String(html)),
  // Compact mode resizes the window (main process owns it).
  setCompact: (compact) => ipcRenderer.invoke("nova:set-compact", compact === true),
  onEvent: (event, callback) => {
    const cb = (params) => callback(params);
    if (!subscribers.has(event)) subscribers.set(event, new Set());
    subscribers.get(event).add(cb);
    if (lastValue.has(event)) callback(lastValue.get(event));
    const unsub = () => {
      subscribers.get(event)?.delete(cb);
    };
    return unsub;
  },
});
