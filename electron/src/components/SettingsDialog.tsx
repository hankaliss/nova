import { useState, useEffect } from "react";
import { Settings } from "../lib/types";
import { Dialog } from "./Dialog";
import { Btn } from "./Btn";
import { Switch } from "./Switch";

export function SettingsDialog({
  open,
  settings,
  onSave,
  onClose,
}: {
  open: boolean;
  settings: Settings | null;
  onSave: (settings: Settings) => void;
  onClose: () => void;
}) {
  const [tab, setTab] = useState(0);
  const [draft, setDraft] = useState<Settings | null>(null);
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
        <div className="text-xs text-subtext">{sub}</div>
      </div>
      <Switch
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
      <div className="mb-4 flex gap-1 rounded-md bg-surface p-1">
        {["General", "LLM"].map((t, i) => (
          <button
            key={t}
            className={`flex-1 cursor-pointer rounded-[4px] px-3 py-1.5 text-xs ${
              tab === i ? "bg-surface-alt text-text" : "text-subtext"
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
          {row(
            "Reduce motion",
            "Disable animations and transitions",
            "reduce_motion",
            "true"
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
            <select
              className="w-full"
              value={draft.num_ctx}
              onChange={(e) =>
                setDraft({ ...draft, num_ctx: e.target.value })
              }
            >
              {["2048", "4096", "8192", "16384", "32768", "65536"].map(
                (n) => (
                  <option key={n} value={n}>
                    {n}
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
              className="w-full rounded-md border border-border bg-surface-alt px-3 py-2 text-sm text-text outline-none focus:border-accent"
              value={draft.ollama_url}
              onChange={(e) =>
                setDraft({ ...draft, ollama_url: e.target.value })
              }
            />
          </div>
          <div>
            <div className="mb-1 text-sm text-text">System prompt</div>
            <textarea
              className="h-[110px] w-full rounded-md border border-border bg-surface-alt px-3 py-2 text-sm text-text outline-none focus:border-accent"
              value={draft.system_prompt}
              onChange={(e) =>
                setDraft({ ...draft, system_prompt: e.target.value })
              }
              placeholder="Default: You are Nova, a helpful local AI desktop assistant..."
            />
          </div>
        </div>
      )}
    </Dialog>
  );
}