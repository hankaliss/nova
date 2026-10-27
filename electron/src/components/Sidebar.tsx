import { useEffect, useState } from "react";
import { Cpu, Pencil, Plus, Trash2 } from "lucide-react";
import { fmtGiB } from "../lib/format";
import { Meter } from "./Meter";
import { NovaMark } from "./NovaMark";

interface Conversation {
  id: number;
  title: string;
  created_at: number;
  updated_at: number;
}

function formatTime(ts: number): string {
  const d = new Date(ts * 1000);
  const now = new Date();
  const diffMins = Math.floor((now.getTime() - d.getTime()) / 60000);
  if (diffMins < 1) return "now";
  if (diffMins < 60) return `${diffMins}m`;
  const diffHours = Math.floor(diffMins / 60);
  if (diffHours < 24) return `${diffHours}h`;
  return `${Math.floor(diffHours / 24)}d`;
}

/** Group label for a conversation's last activity. */
function groupOf(ts: number): string {
  const d = new Date(ts * 1000);
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const day = 86400000;
  if (d.getTime() >= today.getTime()) return "Today";
  if (d.getTime() >= today.getTime() - day) return "Yesterday";
  if (d.getTime() >= today.getTime() - 7 * day) return "Previous 7 days";
  return "Older";
}

function getPreview(
  conversation: Conversation,
  messages: Record<number, string[]>
): string {
  const msgs = messages[conversation.id];
  if (msgs && msgs.length > 0) {
    const last = msgs[msgs.length - 1];
    return last.length > 60 ? last.slice(0, 60) + "…" : last;
  }
  return conversation.title || "New conversation";
}

function MachineCard({
  system,
  onOpen,
}: {
  system: Record<string, any>;
  onOpen: () => void;
}) {
  const gpu = system.gpu || {};
  const hasVram = gpu.vram_total_mib !== undefined;
  return (
    <button
      className="mx-3 mb-3 flex cursor-pointer flex-col gap-2 rounded-xl border border-border bg-bg/60 px-3 py-2.5 text-left transition-colors hover:border-accent/50"
      onClick={onOpen}
      title="Open the Inspector"
    >
      <div className="flex w-full items-center gap-2 text-[12.5px] text-subtext">
        <Cpu size={14} className="shrink-0 text-meta" aria-hidden="true" />
        <span className="truncate">{gpu.name || "This computer"}</span>
        {gpu.temperature_c ? (
          <span
            className={`tabular ml-auto shrink-0 ${
              gpu.temperature_c > 80 ? "text-danger" : "text-meta"
            }`}
          >
            {gpu.temperature_c}°C
          </span>
        ) : null}
      </div>
      {hasVram ? (
        <div className="flex w-full items-center gap-2.5">
          <Meter
            used={gpu.vram_used_mib || 0}
            total={gpu.vram_total_mib || 0}
            label="GPU memory in use"
          />
          <span className="tabular shrink-0 text-[12px] text-meta">
            {fmtGiB(gpu.vram_used_mib)} of {fmtGiB(gpu.vram_total_mib)}
          </span>
        </div>
      ) : (
        <span className="text-[12px] text-meta">Reading GPU…</span>
      )}
    </button>
  );
}

export function Sidebar({
  conversations,
  activeId,
  messages,
  onSelect,
  onNew,
  onDelete,
  onRename,
  ollamaOk,
  connecting,
  system,
  onOpenInspector,
}: {
  conversations: Conversation[];
  activeId: number | null;
  messages: Record<number, string[]>;
  onSelect: (id: number) => void;
  onNew: () => void;
  onDelete: (id: number) => void;
  onRename: (id: number, title: string) => void;
  ollamaOk: boolean;
  connecting: boolean;
  system: Record<string, any>;
  onOpenInspector: () => void;
}) {
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editValue, setEditValue] = useState("");
  const [contextMenu, setContextMenu] = useState<{
    x: number;
    y: number;
    id: number;
  } | null>(null);

  const startRename = (conv: Conversation) => {
    setEditingId(conv.id);
    setEditValue(conv.title || getPreview(conv, messages));
    setContextMenu(null);
  };

  const commitRename = () => {
    if (editingId !== null && editValue.trim()) {
      onRename(editingId, editValue.trim());
    }
    setEditingId(null);
  };

  // Two-step delete: the first click arms the row's button ("Delete?"),
  // a second click within a few seconds deletes. Nothing is lost to a stray
  // click on the hover icon.
  const [confirmId, setConfirmId] = useState<number | null>(null);
  useEffect(() => {
    if (confirmId === null) return;
    const timer = setTimeout(() => setConfirmId(null), 4000);
    return () => clearTimeout(timer);
  }, [confirmId]);

  const handleDeleteClick = (e: React.MouseEvent, id: number) => {
    e.stopPropagation();
    if (confirmId === id) {
      setConfirmId(null);
      onDelete(id);
    } else {
      setConfirmId(id);
    }
  };

  const status = connecting
    ? { dot: "nova-pulse bg-warn", text: "Connecting…" }
    : ollamaOk
      ? { dot: "bg-ok", text: "Ollama running" }
      : { dot: "bg-danger", text: "Ollama offline" };

  // Conversations arrive newest first; keep that order within groups.
  const groups: { label: string; items: Conversation[] }[] = [];
  for (const conv of conversations) {
    const label = groupOf(conv.updated_at);
    const last = groups[groups.length - 1];
    if (last && last.label === label) last.items.push(conv);
    else groups.push({ label, items: [conv] });
  }

  return (
    <nav
      aria-label="Conversations"
      className="flex h-full w-[260px] shrink-0 flex-col border-r border-border bg-surface"
    >
      {/* Brand */}
      <div className="flex items-center gap-2.5 px-4 pb-3 pt-4">
        <NovaMark size={22} />
        <span className="wordmark text-[22px] leading-none text-text">nova</span>
        <span
          className="ml-auto flex items-center gap-1.5 text-[12px] text-meta"
          title={status.text}
        >
          <span className={`h-2 w-2 rounded-full ${status.dot}`} />
          <span className="sr-only">{status.text}</span>
        </span>
      </div>

      <div className="px-3 pb-2">
        <button
          className="flex h-9 w-full cursor-pointer items-center gap-2 rounded-lg border border-border px-3 text-[13.5px] font-medium text-text transition-colors hover:border-accent/60 hover:bg-surface-alt"
          onClick={onNew}
          title="New chat (Ctrl+N)"
        >
          <Plus size={16} className="text-accent" aria-hidden="true" />
          New chat
        </button>
      </div>

      <div className="flex-1 overflow-y-auto px-2 pb-3">
        {groups.map((group) => (
          <section key={group.label} className="mt-3">
            <h3 className="px-2 pb-1 text-[12px] font-medium text-meta">
              {group.label}
            </h3>
            {group.items.map((conv) => {
              const isActive = conv.id === activeId;
              const title = conv.title || getPreview(conv, messages);
              return (
                <div
                  key={conv.id}
                  data-conversation-row
                  className={`group relative flex cursor-pointer items-center gap-1 rounded-lg px-2 py-1.5 transition-colors ${
                    isActive
                      ? "bg-surface-alt text-text"
                      : "text-subtext hover:bg-surface-alt/60 hover:text-text"
                  }`}
                  onClick={() => onSelect(conv.id)}
                  onDoubleClick={() => startRename(conv)}
                  onContextMenu={(e) => {
                    e.preventDefault();
                    setContextMenu({ x: e.clientX, y: e.clientY, id: conv.id });
                  }}
                >
                  {isActive && (
                    <span className="absolute left-0 top-2 bottom-2 w-[3px] rounded-full bg-accent" />
                  )}
                  <div className="min-w-0 flex-1">
                    {editingId === conv.id ? (
                      <input
                        aria-label="Conversation title"
                        className="w-full rounded border-b border-accent bg-transparent text-[13.5px] text-text outline-none"
                        autoFocus
                        value={editValue}
                        onChange={(e) => setEditValue(e.target.value)}
                        onBlur={commitRename}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") commitRename();
                          if (e.key === "Escape") setEditingId(null);
                        }}
                        onClick={(e) => e.stopPropagation()}
                      />
                    ) : (
                      <span
                        data-conversation-title
                        className="block truncate text-[13.5px]"
                        title={title}
                      >
                        {title}
                      </span>
                    )}
                  </div>
                  {confirmId === conv.id ? (
                    <button
                      className="shrink-0 cursor-pointer rounded-md bg-danger/15 px-2 py-0.5 text-[12px] font-semibold text-danger hover:bg-danger/25"
                      onClick={(e) => handleDeleteClick(e, conv.id)}
                      title="Click again to delete this conversation"
                      aria-label="Confirm delete conversation"
                    >
                      Delete?
                    </button>
                  ) : (
                    <>
                      <span className="tabular shrink-0 text-[12px] text-meta group-hover:hidden group-focus-within:hidden">
                        {formatTime(conv.updated_at)}
                      </span>
                      <button
                        className="hidden shrink-0 cursor-pointer rounded-md p-1 text-meta hover:text-danger group-hover:block group-focus-within:block focus:block"
                        onClick={(e) => handleDeleteClick(e, conv.id)}
                        title="Delete conversation"
                        aria-label="Delete conversation"
                      >
                        <Trash2 size={14} />
                      </button>
                    </>
                  )}
                </div>
              );
            })}
          </section>
        ))}

        {conversations.length === 0 && (
          <p className="px-3 py-6 text-center text-[13px] text-meta">
            Your conversations will appear here.
          </p>
        )}
      </div>

      <MachineCard system={system} onOpen={onOpenInspector} />

      {contextMenu && (
        <>
          <div
            className="fixed inset-0 z-40"
            onClick={() => setContextMenu(null)}
          />
          <div
            role="menu"
            className="fixed z-50 min-w-[150px] overflow-hidden rounded-xl border border-border bg-surface-alt py-1 shadow-2xl"
            style={{ left: contextMenu.x, top: contextMenu.y }}
          >
            <button
              role="menuitem"
              className="flex w-full cursor-pointer items-center gap-2 px-3 py-2 text-[13px] text-text hover:bg-surface"
              onClick={() => {
                const conv = conversations.find((c) => c.id === contextMenu.id);
                if (conv) startRename(conv);
              }}
            >
              <Pencil size={14} />
              Rename
            </button>
            <button
              role="menuitem"
              className="flex w-full cursor-pointer items-center gap-2 px-3 py-2 text-[13px] text-danger hover:bg-surface"
              onClick={() => {
                // Arm the row's inline confirm instead of deleting outright.
                setConfirmId(contextMenu.id);
                setContextMenu(null);
              }}
            >
              <Trash2 size={14} />
              Delete
            </button>
          </div>
        </>
      )}
    </nav>
  );
}
