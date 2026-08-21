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
