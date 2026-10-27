import { useState, useEffect } from "react";
import { Settings } from "../lib/types";
import { Dialog } from "./Dialog";
import { Btn } from "./Btn";
import { Switch } from "./Switch";
import { ShortcutField, ShortcutStatus } from "./ShortcutField";
import { eventScope } from "../lib/events";

export function SettingsDialog({
  open,
  settings,
  onSave,
  onClose,
  onRunSetup,
}: {
  open: boolean;
  settings: Settings | null;
  onSave: (settings: Settings) => void;
  onClose: () => void;
  onRunSetup?: () => void;
}) {
  const [tab, setTab] = useState(0);
  const [draft, setDraft] = useState<Settings | null>(null);
  const [shortcutStatus, setShortcutStatus] = useState<ShortcutStatus | null>(null);
  useEffect(() => {
    const { on, off } = eventScope();
    on("shortcut_status", (data) => setShortcutStatus(data));
    return off;
  }, []);
  useEffect(() => {
    if (open && settings) {
      setDraft({ ...settings });
      setTab(0);
    }
  }, [open, settings]);
  if (!open || !draft) return null;

  const row = (
    title: string,
    sub: string,
    key: string,
    on: string
  ) => (
    <div className="flex items-center justify-between gap-4">
      <div>
        <div className="text-sm text-text">{title}</div>
        <div className="text-[12.5px] text-subtext">{sub}</div>
      </div>
      <Switch
        label={title}
        checked={draft[key] === on}
        onChange={(v) => setDraft({ ...draft, [key]: v ? on : "false" })}
      />
    </div>
  );

  return (
    <Dialog
      title="Settings"
      onClose={onClose}
      width={560}
      footer={
        <>
          <Btn variant="ghost" onClick={onClose}>
            Cancel
          </Btn>
          <Btn variant="accent" onClick={() => onSave(draft)}>
            Save
          </Btn>
        </>
      }
    >
      <div role="tablist" className="mb-4 flex gap-1 rounded-xl bg-bg p-1">
        {["General", "Model"].map((t, i) => (
          <button
            key={t}
            role="tab"
            aria-selected={tab === i}
            className={`flex-1 cursor-pointer rounded-lg px-3 py-1.5 text-[13px] transition-colors ${
              tab === i ? "bg-surface-alt font-semibold text-text" : "text-meta hover:text-text"
            }`}
            onClick={() => setTab(i)}
          >
            {t}
          </button>
        ))}
      </div>

      {tab === 0 && (
        <div className="flex flex-col gap-3">
          {row(
            "Close to tray",
            "Closing the window hides Nova instead of quitting",
            "close_to_tray",
            "true"
          )}
          {row(
            "Auto-approve WRITE tools",
            "File/system modifications run without asking (READ tools always run)",
            "auto_approve_writes",
            "true"
          )}
          {row(
            "Always on top",
            "Keep the Nova window above other windows",
            "always_on_top",
            "true"
          )}
          <ShortcutField
            value={draft.toggle_shortcut ?? ""}
            savedValue={settings?.toggle_shortcut ?? ""}
            status={shortcutStatus}
            onChange={(text) => setDraft((d) => (d ? { ...d, toggle_shortcut: text } : d))}
          />
          {row(
            "Reduce motion",
            "Disable animations and transitions",
            "reduce_motion",
            "true"
          )}
          {onRunSetup && (
            <div className="flex items-center justify-between gap-4">
              <div>
                <div className="text-sm text-text">Setup</div>
                <div className="text-[12.5px] text-subtext">Ollama, model, web search and shortcut, step by step</div>
              </div>
              <Btn onClick={onRunSetup}>Run setup again</Btn>
            </div>
          )}
        </div>
      )}

      {tab === 1 && (
        <div className="flex flex-col gap-4">
          <div>
            <div className="mb-1 flex justify-between">
              <span className="text-sm text-text">Temperature</span>
              <span className="text-sm font-bold text-accent">
                {draft.temperature}
              </span>
            </div>
            <input
              type="range"
              min={0}
              max={1.5}
              step={0.05}
              className="w-full"
              value={parseFloat(draft.temperature || "0.7")}
              onChange={(e) =>
                setDraft({ ...draft, temperature: e.target.value })
              }
            />
          </div>
          <div>
            <div className="mb-1 text-sm text-text">
              Context size (num_ctx)
            </div>
            <p className="mb-1.5 text-[12.5px] text-meta">
              How much conversation the model remembers. Automatic picks the
              largest size that keeps each model fully on your GPU (measured
              when you select it). A fixed size that doesn't fit makes answers
              many times slower.
            </p>
            <select
              className="w-full"
              value={draft.num_ctx}
              onChange={(e) =>
                setDraft({ ...draft, num_ctx: e.target.value })
              }
            >
              <option value="auto">Automatic (largest that fits the GPU)</option>
              {["2048", "4096", "8192", "12288", "16384", "24576", "32768", "65536"].map(
                (n) => (
                  <option key={n} value={n}>
                    {Number(n).toLocaleString()} tokens
                  </option>
                )
              )}
            </select>
          </div>
          <div>
            <div className="mb-1 text-sm text-text">
              Ollama URL (applies on next start)
            </div>
            <input
              className="w-full rounded-lg border border-border bg-bg px-3 py-2 text-sm text-text outline-none focus:border-accent"
              value={draft.ollama_url}
              onChange={(e) =>
                setDraft({ ...draft, ollama_url: e.target.value })
              }
            />
          </div>
          <div>
            <div className="mb-1 flex items-baseline justify-between gap-2">
              <span className="text-sm text-text">System prompt</span>
              <span
                className="truncate font-mono text-[12px] text-meta"
                title="Where the active system prompt comes from"
              >
                {draft.system_prompt_source || "source unknown"}
              </span>
            </div>
            <textarea
              className="h-[150px] w-full rounded-lg border border-border bg-bg px-3 py-2 text-sm text-text outline-none focus:border-accent"
              value={draft.system_prompt}
              onChange={(e) =>
                setDraft({ ...draft, system_prompt: e.target.value })
              }
              placeholder="Enter a custom system prompt, or leave empty for the bundled default…"
            />
            <div className="mt-1 text-[12px] leading-snug text-subtext">
              Saving writes this to ~/.config/nova/agent.md and applies it
              immediately. Clearing the field reverts to Nova's bundled prompt.
            </div>
          </div>
        </div>
      )}
    </Dialog>
  );
}