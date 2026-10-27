import { useEffect, useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import { Memory } from "../lib/types";
import { Btn } from "./Btn";
import { Dialog } from "./Dialog";

function savedOn(ts: number): string {
  return new Date(ts * 1000).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

export function MemoriesDialog({
  open,
  memories,
  onAdd,
  onDelete,
  onClose,
}: {
  open: boolean;
  memories: Memory[];
  onAdd: (content: string) => void;
  onDelete: (id: number) => void;
  onClose: () => void;
}) {
  const [draft, setDraft] = useState("");
  // Two-step delete, like conversations: first click arms, second deletes.
  const [confirmId, setConfirmId] = useState<number | null>(null);
  useEffect(() => {
    if (confirmId === null) return;
    const timer = setTimeout(() => setConfirmId(null), 4000);
    return () => clearTimeout(timer);
  }, [confirmId]);

  if (!open) return null;

  const add = () => {
    const text = draft.trim();
    if (!text) return;
    onAdd(text);
    setDraft("");
  };

  return (
    <Dialog title="Memories" onClose={onClose} width={600}>
      <p className="mb-3 text-[13px] text-subtext">
        Facts Nova keeps between conversations. It only saves one when you ask
        (and you confirm it), and adds the ones that match your message to its
        instructions.
      </p>

      <form
        className="mb-4 flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          add();
        }}
      >
        <input
          aria-label="New memory"
          className="min-w-0 flex-1 rounded-lg border border-border bg-bg px-3 py-2 text-[13.5px] text-text outline-none placeholder:text-faint focus:border-accent"
          placeholder="e.g. The NAS is at 192.168.1.20"
          maxLength={500}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
        />
        <Btn variant="accent" disabled={!draft.trim()} className="h-[38px]">
          <Plus size={15} aria-hidden="true" />
          Add
        </Btn>
      </form>

      {memories.length === 0 ? (
        <p className="rounded-xl border border-dashed border-border px-4 py-6 text-center text-[13px] text-meta">
          Nothing yet. Tell Nova "remember that…" in a chat, or add a fact here.
        </p>
      ) : (
        <ul className="flex flex-col gap-1.5">
          {memories.map((m) => (
            <li
              key={m.id}
              className="group flex items-start gap-3 rounded-xl border border-border bg-bg/50 px-3.5 py-2.5"
            >
              <div className="min-w-0 flex-1">
                <p className="text-[13.5px] text-text">{m.content}</p>
                <p className="tabular mt-0.5 text-[12px] text-meta">Saved {savedOn(m.created_at)}</p>
              </div>
              {confirmId === m.id ? (
                <button
                  className="shrink-0 cursor-pointer rounded-md bg-danger/15 px-2 py-1 text-[12px] font-semibold text-danger hover:bg-danger/25"
                  onClick={() => {
                    setConfirmId(null);
                    onDelete(m.id);
                  }}
                  aria-label="Confirm forget memory"
                >
                  Forget?
                </button>
              ) : (
                <button
                  className="shrink-0 cursor-pointer rounded-md p-1.5 text-meta opacity-0 transition-opacity hover:text-danger focus:opacity-100 group-hover:opacity-100"
                  onClick={() => setConfirmId(m.id)}
                  title="Forget this"
                  aria-label="Forget this memory"
                >
                  <Trash2 size={15} />
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
    </Dialog>
  );
}
