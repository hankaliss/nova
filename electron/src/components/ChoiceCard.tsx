import { useEffect, useState } from "react";
import { PendingChoices } from "../lib/types";
import { NovaMark } from "./NovaMark";

/**
 * Inline multiple-choice card: the LLM called nova.presentChoices and is
 * waiting for the user to pick one option. Selection is via mouse, arrow
 * keys + Enter, or number keys 1-9 — all send the index back through the
 * choice_select RPC.
 */
export function ChoiceCard({
  choices,
  onSelect,
}: {
  choices: PendingChoices;
  onSelect: (index: number) => void;
}) {
  const [highlight, setHighlight] = useState(0);
  const count = choices.options.length;

  useEffect(() => {
    // Keep the highlight inside the option range when options change.
    setHighlight((h) => Math.min(h, count - 1));
  }, [count]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setHighlight((h) => (h + 1) % count);
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setHighlight((h) => (h - 1 + count) % count);
      } else if (e.key === "Enter") {
        e.preventDefault();
        onSelect(highlight);
      } else {
        const n = parseInt(e.key, 10);
        if (!Number.isNaN(n) && n >= 1 && n <= count) onSelect(n - 1);
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [highlight, count, onSelect]);

  return (
    <div className="msg-in mb-6 flex gap-3">
      <div className="pt-[3px]">
        <NovaMark size={18} />
      </div>
      <div className="min-w-0 flex-1">
        <p className="text-[14.5px] text-text">{choices.question}</p>
        <div role="listbox" aria-label={choices.question} className="mt-3 flex max-w-[560px] flex-col gap-1.5">
          {choices.options.map((opt, i) => {
            const active = i === highlight;
            return (
              <button
                key={i}
                role="option"
                aria-selected={active}
                className={`group flex cursor-pointer items-center gap-3 rounded-xl border px-3.5 py-2.5 text-left text-[14px] transition-colors ${
                  active
                    ? "border-accent/70 bg-accent/10 text-text"
                    : "border-border bg-surface text-subtext hover:border-accent/50 hover:text-text"
                }`}
                onClick={() => onSelect(i)}
                onMouseEnter={() => setHighlight(i)}
              >
                <kbd
                  className={`tabular flex h-6 w-6 shrink-0 items-center justify-center rounded-md border text-[12px] ${
                    active
                      ? "border-accent/60 text-accent"
                      : "border-border text-meta group-hover:text-accent"
                  }`}
                >
                  {i + 1}
                </kbd>
                <span className="min-w-0">{opt}</span>
              </button>
            );
          })}
        </div>
        <p className="mt-2 text-[12px] text-meta">
          Click an option, press its number, or use the arrow keys and Enter.
        </p>
      </div>
    </div>
  );
}
