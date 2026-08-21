import { useMemo, useState } from "react";
import { Check, Copy } from "lucide-react";
import { Message } from "../lib/types";
import { Artifact, extractArtifacts } from "../lib/artifacts";
import { Markdown } from "./Markdown";
import { ThinkingText } from "./ThinkingText";
import { ToolCard } from "./ToolCard";

export function ChatBubble({
  message,
  index = 0,
  onCopy,
  onOpenArtifact,
  activeArtifactId,
}: {
  message: Message;
  index?: number;
  onCopy: (text: string) => void;
  onOpenArtifact?: (artifact: Artifact) => void;
  activeArtifactId?: string;
}) {
  const [hover, setHover] = useState(false);
  const [copied, setCopied] = useState(false);
  const user = message.role === "user";

  const artifacts = useMemo(() => {
    if (user || !message.content) return [];
    return extractArtifacts(message.content, index);
  }, [user, message.content, index]);

  return (
    <div
      className="msg-in mb-4 max-w-[82ch]"
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
    >
      <div
        className={`text-mono text-[11px] leading-none ${
          user ? "text-warn" : "text-accent"
        }`}
      >
        <b className="font-bold tracking-[0.08em]">{user ? "you" : "nova"}</b>
        <span className="text-faint"> ›</span>
      </div>
      <div className="mt-1 pl-[3.5ch]">
        {(message.content || message.streaming) && (
          <div>
            {message.content && <Markdown text={message.content} user={user} />}
            {message.streaming &&
              (message.content ? (
                <span className="caret shrink-0" />
              ) : (
                <ThinkingText />
              ))}
          </div>
        )}
        {artifacts.length > 0 && (
          <div className="mt-2 flex flex-wrap gap-2">
            {artifacts.map((art) => {
              const isActive = activeArtifactId === art.id;
              return (
                <button
                  key={art.id}
                  className={`flex cursor-pointer items-center gap-1.5 rounded-none border px-2.5 py-1 font-mono text-[11px] transition-colors ${
                    isActive
                      ? "border-accent bg-surface-alt font-bold text-accent-2 shadow-[0_0_8px_rgba(138,230,140,0.3)]"
                      : "border-border bg-surface text-accent hover:border-accent hover:bg-surface-alt hover:text-accent-2"
                  }`}
                  onClick={() => onOpenArtifact?.(art)}
                  title="View interactive artifact preview"
                >
                  <span className="text-accent">◈</span>
                  <span>{art.title}</span>
                  <span className="text-faint">{isActive ? "●" : "→"}</span>
                </button>
              );
            })}
          </div>
        )}
        {message.tools.length > 0 && (
          <div className="mt-1.5 flex flex-col gap-1">
            {message.tools.map((t, i) => (
              <ToolCard key={i} tool={t} />
            ))}
          </div>
        )}
        {!user && message.content && (
          <button
            className={`mt-1 flex cursor-pointer items-center gap-1 rounded px-1.5 py-0.5 text-[10px] text-meta transition-opacity duration-150 hover:bg-surface-alt hover:text-subtext ${
              hover ? "opacity-100" : "opacity-0"
            }`}
            onClick={() => {
              onCopy(message.content);
              setCopied(true);
              window.setTimeout(() => setCopied(false), 1200);
            }}
          >
            {copied ? <Check size={10} /> : <Copy size={10} />}
            {copied ? "copied" : "copy"}
          </button>
        )}
      </div>
    </div>
  );
}
