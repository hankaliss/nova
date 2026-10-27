import { useEffect, useRef, useState } from "react";
import { Btn } from "./Btn";
import { fromEvent } from "../lib/shortcutKeys";

export interface ShortcutStatus {
  available: boolean;
  shortcut: string;
  error: string;
}

/** Records a global shortcut; Escape cancels. */
export function ShortcutField({
  value,
  savedValue,
  status,
  onChange,
}: {
  value: string;
  /** The setting as last saved (what the sidecar tried to register). */
  savedValue: string;
  status: ShortcutStatus | null;
  onChange: (text: string) => void;
}) {
  const [recording, setRecording] = useState(false);
  const [hint, setHint] = useState("");
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;

  useEffect(() => {
    if (!recording) return;
    // Nova's current key must not hide the window while a new one is typed.
    window.nova.call("shortcutRecording", { active: true });
    const onKey = (e: KeyboardEvent) => {
      // Capture phase on window: runs before the dialog's Escape/Tab
      // handling and the app's Ctrl+N/T/M shortcuts, which must not fire.
      e.preventDefault();
      e.stopPropagation();
      if (e.key === "Escape" && !e.metaKey && !e.ctrlKey && !e.altKey) {
        setRecording(false);
        return;
      }
      const result = fromEvent(e);
      if (!result) return;
      if (result.error) {
        setHint(result.error);
        return;
      }
      onChangeRef.current(result.text!);
      setRecording(false);
    };
    window.addEventListener("keydown", onKey, true);
    return () => {
      window.removeEventListener("keydown", onKey, true);
      window.nova.call("shortcutRecording", { active: false });
    };
  }, [recording]);

  let note = "";
  let noteClass = "text-meta";
  if (recording) {
    note = hint || "Press the keys, Escape to cancel";
  } else if (status && !status.available) {
    note = "Needs KDE Plasma's shortcut service";
  } else if (value !== savedValue) {
    note = "Applied when you save";
  } else if (status?.error) {
    note = status.error;
    noteClass = "text-danger";
  } else if (status?.shortcut) {
    note = "Active in KDE (also listed in System Settings → Shortcuts)";
    noteClass = "text-ok";
  }

  return (
    <div className="flex items-start justify-between gap-4">
      <div>
        <div className="text-sm text-text">Show/hide shortcut</div>
        <div className="text-[12.5px] text-subtext">
          A key that shows or hides Nova from anywhere
        </div>
        {note && (
          <div className={`mt-1 text-[12px] ${noteClass}`} aria-live="polite">
            {note}
          </div>
        )}
      </div>
      <div className="flex shrink-0 items-center gap-1.5">
        <button
          className={`tabular min-w-[120px] cursor-pointer rounded-lg border px-2.5 py-1.5 text-center text-[13px] ${
            recording
              ? "border-accent bg-accent/10 text-text"
              : "border-border bg-bg text-text hover:border-accent/60"
          }`}
          onClick={() => {
            setHint("");
            setRecording((r) => !r);
          }}
          aria-label={value ? `Shortcut ${value}, click to change` : "Set a shortcut"}
        >
          {recording ? "Press keys…" : value || "None"}
        </button>
        {value && !recording && (
          <Btn variant="ghost" onClick={() => onChange("")}>
            Clear
          </Btn>
        )}
      </div>
    </div>
  );
}
