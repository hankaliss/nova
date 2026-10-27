import { useEffect, useRef, useState } from "react";
import { Check, ChevronDown } from "lucide-react";
import { ModelDetail } from "../lib/types";

function vramEstimate(size: number): string {
  if (!size) return "";
  return `about ${(size * 1.2 / 1e9).toFixed(1)} GB`;
}

export function ModelPicker({
  models,
  current,
  ollamaOk,
  busy,
  onSelect,
}: {
  models: ModelDetail[];
  current: string;
  ollamaOk: boolean;
  busy: boolean;
  onSelect: (name: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open]);

  const disabled = !ollamaOk || (models.length === 0 && !current);
  const label = !ollamaOk ? "Ollama offline" : current || "Choose a model";

  return (
    <div ref={ref} className="relative min-w-0 max-w-[320px]">
      <button
        className="flex h-8 max-w-full cursor-pointer items-center gap-2 rounded-lg px-2.5 text-[14px] font-semibold text-text transition-colors hover:bg-surface-alt disabled:cursor-default disabled:opacity-50"
        onClick={() => !disabled && setOpen((p) => !p)}
        disabled={disabled}
        title="Choose the model"
        aria-haspopup="listbox"
        aria-expanded={open}
      >
        <span
          className={`h-2 w-2 shrink-0 rounded-full ${ollamaOk ? "bg-ok" : "bg-danger"}`}
          aria-hidden="true"
        />
        <span className="truncate">{busy ? "Connecting…" : label}</span>
        <ChevronDown
          size={15}
          className={`shrink-0 text-meta transition-transform ${open ? "rotate-180" : ""}`}
          aria-hidden="true"
        />
      </button>

      {open && !disabled && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />
          <div
            role="listbox"
            aria-label="Local models"
            className="absolute left-0 z-50 mt-1 w-[380px] overflow-hidden rounded-xl border border-border bg-surface-alt p-1 shadow-2xl"
          >
            <div className="max-h-[340px] overflow-y-auto">
              {models.map((m) => {
                const active = m.name === current;
                const details = [
                  m.parameter_size,
                  m.quantization,
                  m.context_length
                    ? `${m.context_length.toLocaleString()} ctx`
                    : "",
                  vramEstimate(m.size),
                  m.capabilities?.includes("tools") ? "uses tools" : "",
                ]
                  .filter(Boolean)
                  .join(", ");
                return (
                  <button
                    key={m.name}
                    role="option"
                    aria-selected={active}
                    className={`flex w-full cursor-pointer items-center justify-between gap-3 rounded-lg px-3 py-2 text-left transition-colors hover:bg-surface ${
                      active ? "bg-surface" : ""
                    }`}
                    onClick={() => {
                      onSelect(m.name);
                      setOpen(false);
                    }}
                  >
                    <div className="min-w-0">
                      <div
                        className={`truncate text-[14px] ${
                          active ? "font-semibold text-text" : "text-text"
                        }`}
                      >
                        {m.name}
                      </div>
                      {details && (
                        <div className="mt-0.5 truncate text-[12px] text-meta">
                          {details}
                        </div>
                      )}
                    </div>
                    {active && (
                      <Check size={16} className="shrink-0 text-accent" aria-hidden="true" />
                    )}
                  </button>
                );
              })}
              {models.length === 0 && (
                <div className="px-3 py-4 text-center text-[13px] text-meta">
                  No local models found. Pull one with ollama pull.
                </div>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}