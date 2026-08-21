import { useState } from "react";
import { Pencil, Plus, Trash2 } from "lucide-react";

interface Conversation {
  id: number;
  title: string;
  created_at: number;
  updated_at: number;
}

function formatTime(ts: number): string {
  const d = new Date(ts * 1000);
  const now = new Date();
  const diffMs = now.getTime() - d.getTime();
  const diffMins = Math.floor(diffMs / 60000);
  if (diffMins < 1) return "now";
  if (diffMins < 60) return `${diffMins}m`;
  const diffHours = Math.floor(diffMins / 60);
  if (diffHours < 24) return `${diffHours}h`;
  const diffDays = Math.floor(diffHours / 24);
  return `${diffDays}d`;
}

function getPreview(
  conversation: Conversation,
  messages: Record<number, string[]>
): string {
  const msgs = messages[conversation.id];
  if (msgs && msgs.length > 0) {
    const last = msgs[msgs.length - 1];
    return last.length > 40 ? last.slice(0, 40) + "…" : last;
  }
  return conversation.title || "New conversation";
}

export function Sidebar({
  conversations,
  activeId,
  messages,
  onSelect,
  onNew,
  onDelete,
  onRename,
}: {
  conversations: Conversation[];
  activeId: number | null;
  messages: Record<number, string[]>;
  onSelect: (id: number) => void;
  onNew: () => void;
  onDelete: (id: number) => void;
  onRename: (id: number, title: string) => void;
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

  const handleDeleteClick = (e: React.MouseEvent, id: number) => {
    e.stopPropagation();
    onDelete(id);
  };

  return (
    <div className="flex h-full w-[240px] shrink-0 flex-col border-r border-border bg-surface">
      <div className="flex flex-col gap-2 px-3 pb-1 pt-3">
        <span className="text-[9px] font-bold tracking-[0.22em] text-faint">
          CONVERSATIONS
        </span>
        <button
          className="flex cursor-pointer items-center justify-center gap-1.5 rounded-none border border-dashed border-faint py-1.5 font-mono text-[11px] text-subtext transition-colors hover:border-accent hover:text-accent"
          onClick={onNew}
        >
          <Plus size={12} strokeWidth={2} />
          new chat
        </button>
      </div>

      <div className="flex-1 overflow-y-auto px-1.5 pb-2 pt-1.5">
        {conversations.map((conv) => {
          const isActive = conv.id === activeId;
          return (
            <div
              key={conv.id}
              className={`group relative flex cursor-pointer items-start gap-1 rounded-none px-2 py-1.5 text-left transition-colors ${
                isActive
                  ? "bg-surface-alt text-accent-2"
                  : "text-subtext hover:bg-surface-alt hover:text-text"
              }`}
              onClick={() => {
                onSelect(conv.id);
              }}
              onContextMenu={(e) => {
                e.preventDefault();
                setContextMenu({ x: e.clientX, y: e.clientY, id: conv.id });
              }}
            >
              <span
                className={`w-3 shrink-0 select-none font-mono text-[11px] leading-4 ${
                  isActive ? "text-accent" : "text-transparent"
                }`}
              >
                ▸
              </span>
              <div className="min-w-0 flex-1">
                {editingId === conv.id ? (
                  <input
                    className="w-full bg-transparent font-mono text-xs font-bold text-text outline-none"
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
                  <span className="block truncate font-mono text-xs font-bold">
                    {conv.title || getPreview(conv, messages)}
                  </span>
                )}
                <span className="mt-0.5 block truncate text-[10px] text-meta">
                  {getPreview(conv, messages)}
                </span>
                <span className="mt-0.5 block font-mono text-[9px] text-faint">
                  {formatTime(conv.updated_at)}
                </span>
              </div>
              <button
                className="mt-0.5 shrink-0 cursor-pointer rounded px-1.5 py-1 text-meta opacity-0 transition-opacity group-hover:opacity-100 hover:text-danger"
                onClick={(e) => handleDeleteClick(e, conv.id)}
                title="Delete conversation"
              >
                <Trash2 size={12} strokeWidth={2} />
              </button>
            </div>
          );
        })}

        {conversations.length === 0 && (
          <div className="px-2 py-4 text-center font-mono text-[11px] text-faint">
            // no conversations yet
          </div>
        )}
      </div>

      {contextMenu && (
        <>
          <div
            className="fixed inset-0 z-40"
            onClick={() => setContextMenu(null)}
          />
          <div
            className="fixed z-50 min-w-[140px] rounded-none border border-border bg-surface shadow-lg"
            style={{ left: contextMenu.x, top: contextMenu.y }}
          >
            <button
              className="flex w-full cursor-pointer items-center gap-2 px-3 py-2 font-mono text-xs text-text hover:bg-surface-alt"
              onClick={() => {
                const conv = conversations.find((c) => c.id === contextMenu.id);
                if (conv) startRename(conv);
              }}
            >
              <Pencil size={12} />
              Rename
            </button>
            <button
              className="flex w-full cursor-pointer items-center gap-2 px-3 py-2 font-mono text-xs text-danger hover:bg-surface-alt"
              onClick={() => {
                onDelete(contextMenu.id);
                setContextMenu(null);
              }}
            >
              <Trash2 size={12} />
              Delete
            </button>
          </div>
        </>
      )}
    </div>
  );
}
