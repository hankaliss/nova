import { useMemo, useState } from "react";
import { AppWindow, Check, Copy, RotateCw } from "lucide-react";
import { Message } from "../lib/types";
import { Artifact, displayTitle, extractArtifacts } from "../lib/artifacts";
import { Markdown } from "./Markdown";
import { NovaMark } from "./NovaMark";
import { ThinkingText } from "./ThinkingText";
import { ToolCard } from "./ToolCard";

function fmtTime(ts?: number): string {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  const now = new Date();
  if (d.toDateString() === now.toDateString())
    return d.toTimeString().slice(0, 5);
  const days = Math.floor((now.getTime() - d.getTime()) / 86400000);
  if (days < 7)
    return d.toLocaleDateString(undefined, { weekday: "short" });
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export function ChatBubble({
  message,
  index = 0,
  onCopy,
  artifacts: indexed,
  onOpenArtifact,
  activeArtifactId,
  onRegenerate,
}: {
  message: Message;
  index?: number;
  onCopy: (text: string) => void;
  /** This message's artifacts from the conversation index (with versions). */
  artifacts?: Artifact[];
  onOpenArtifact?: (artifact: Artifact) => void;
  activeArtifactId?: string;
  onRegenerate?: (index: number) => void;
}) {
  const [hover, setHover] = useState(false);
  const [copied, setCopied] = useState(false);
  const user = message.role === "user";

  const artifacts = useMemo(() => {
    if (indexed) return indexed;
    if (user || !message.content) return [];
    return extractArtifacts(message.content, index);
  }, [indexed, user, message.content, index]);

  const time = !message.streaming && message.content ? fmtTime(message.created_at) : "";

  if (user) {
    return (
      <div className="msg-in mb-6 flex flex-col items-end">
        <div className="max-w-[85%] rounded-2xl rounded-br-md bg-userBubble px-4 py-2.5">
          {message.content && <Markdown text={message.content} user />}
        </div>
        {time && <span className="tabular mt-1 px-1 text-[12px] text-meta">{time}</span>}
      </div>
    );
  }

  return (
    <div
      className="msg-in mb-6 flex gap-3"
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
    >
      <div className="pt-[3px]">
        <NovaMark size={18} flare={!!message.streaming} />
      </div>
      <div className="min-w-0 flex-1">
        {(message.content || message.streaming) && (
          <div>
            {message.content && (
              <Markdown text={message.content} streaming={!!message.streaming} />
            )}
            {message.streaming && !message.content && <ThinkingText />}
          </div>
        )}
        {artifacts.length > 0 && (
          <div className="mt-3 flex flex-wrap gap-2">
            {artifacts.map((art) => {
              const isActive = activeArtifactId === art.id;
              return (
                <button
                  key={art.id}
                  className={`flex cursor-pointer items-center gap-2 rounded-xl border px-3 py-2 text-left text-[13px] transition-colors ${
                    isActive
                      ? "border-accent bg-accent/10 text-text"
                      : "border-border bg-surface text-subtext hover:border-accent/60 hover:text-text"
                  }`}
                  onClick={() => onOpenArtifact?.(art)}
                  title="Open the preview panel"
                  aria-pressed={isActive}
                >
                  <AppWindow size={15} className="shrink-0 text-accent" aria-hidden="true" />
                  <span className="font-medium">{displayTitle(art)}</span>
                  {art.versions > 1 && (
                    <span className="tabular rounded-md bg-surface-alt px-1.5 text-[12px] text-subtext">
                      v{art.version}
                    </span>
                  )}
                  <span className="text-[12px] text-meta">
                    {isActive ? "Open" : art.tool ? "Report" : "Preview"}
                  </span>
                </button>
              );
            })}
          </div>
        )}
        {message.tools.length > 0 && (
          <div className="mt-2.5 flex flex-col gap-1.5">
            {message.tools.map((t, i) => (
              <ToolCard key={i} tool={t} />
            ))}
          </div>
        )}
        {message.content && (
          <div
            className={`mt-1.5 flex items-center gap-0.5 transition-opacity duration-150 focus-within:opacity-100 ${
              hover ? "opacity-100" : "opacity-0"
            }`}
          >
            <button
              className="flex cursor-pointer items-center gap-1.5 rounded-md px-2 py-1 text-[12px] text-meta hover:bg-surface-alt hover:text-text"
              onClick={() => {
                onCopy(message.content);
                setCopied(true);
                window.setTimeout(() => setCopied(false), 1200);
              }}
            >
              {copied ? <Check size={13} /> : <Copy size={13} />}
              {copied ? "Copied" : "Copy"}
            </button>
            {!message.streaming && onRegenerate && (
              <button
                className="flex cursor-pointer items-center gap-1.5 rounded-md px-2 py-1 text-[12px] text-meta hover:bg-surface-alt hover:text-text"
                onClick={() => onRegenerate(index)}
                title="Write this answer again"
              >
                <RotateCw size={13} />
                Regenerate
              </button>
            )}
            {time && <span className="tabular ml-1 text-[12px] text-meta">{time}</span>}
          </div>
        )}
      </div>
    </div>
  );
}
