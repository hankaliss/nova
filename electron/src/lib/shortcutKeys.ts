/** Turning a key press into the shortcut text the sidecar accepts. */

// KeyboardEvent.code → the key names the sidecar's parser accepts
// (nova/kde/shortcut.py).
const NAMED: Record<string, string> = {
  Space: "Space",
  Tab: "Tab",
  Backspace: "Backspace",
  Enter: "Return",
  Insert: "Insert",
  Delete: "Delete",
  Pause: "Pause",
  PrintScreen: "Print",
  Home: "Home",
  End: "End",
  ArrowLeft: "Left",
  ArrowUp: "Up",
  ArrowRight: "Right",
  ArrowDown: "Down",
  PageUp: "PgUp",
  PageDown: "PgDown",
  Backquote: "`",
  Minus: "-",
  Equal: "=",
  BracketLeft: "[",
  BracketRight: "]",
  Semicolon: ";",
  Quote: "'",
  Comma: ",",
  Period: ".",
  Slash: "/",
  Backslash: "\\",
};

function keyName(code: string): string | null {
  if (/^Key[A-Z]$/.test(code)) return code.slice(3);
  if (/^Digit[0-9]$/.test(code)) return code.slice(5);
  if (/^F([1-9]|1[0-9]|2[0-4])$/.test(code)) return code;
  return NAMED[code] ?? null;
}

/** "Ctrl+Alt+N" from a key press, or an error for keys that can't be global. */
export function fromEvent(
  e: Pick<KeyboardEvent, "code" | "metaKey" | "ctrlKey" | "altKey" | "shiftKey">
): { text?: string; error?: string } | null {
  const key = keyName(e.code);
  if (key === null) return null; // a modifier on its own, or unsupported
  const mods = [
    e.metaKey && "Meta",
    e.ctrlKey && "Ctrl",
    e.altKey && "Alt",
    e.shiftKey && "Shift",
  ].filter(Boolean) as string[];
  const standalone = /^F\d+$/.test(key) || key === "Pause" || key === "Print";
  if (!standalone && !(e.metaKey || e.ctrlKey || e.altKey)) {
    return { error: "Add Meta, Ctrl or Alt" };
  }
  return { text: [...mods, key].join("+") };
}
