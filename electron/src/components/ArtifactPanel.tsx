import { useState } from "react";
import { Check, Code2, Copy, ExternalLink, Eye, RefreshCw, X } from "lucide-react";
import { Artifact } from "../lib/artifacts";

export function ArtifactPanel({
  artifact,
  onClose,
}: {
  artifact: Artifact;
  onClose: () => void;
}) {
  const [tab, setTab] = useState<"preview" | "code">("preview");
  const [copied, setCopied] = useState(false);
  const [key, setKey] = useState(0);

  const handleCopy = () => {
    navigator.clipboard.writeText(artifact.content);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  const handleOpenBrowser = () => {
    const blob = new Blob([artifact.content], { type: "text/html" });
    const url = URL.createObjectURL(blob);
    window.open(url, "_blank");
  };

  return (
    <div className="flex h-full w-[480px] shrink-0 flex-col border-l border-border bg-surface shadow-2xl transition-all duration-150">
      {/* Top Header */}
      <div className="flex h-10 items-center justify-between border-b border-border px-3 shrink-0">
        <div className="flex min-w-0 items-center gap-2">
          <span className="font-mono text-xs text-accent">◈</span>
          <span className="truncate font-mono text-[11.5px] font-bold text-accent-2" title={artifact.title}>
            {artifact.title}
          </span>
        </div>

        {/* View Switcher Tabs */}
        <div className="flex items-center gap-1">
          <div className="flex rounded-none border border-border bg-bg p-0.5 font-mono text-[10.5px]">
            <button
              className={`flex cursor-pointer items-center gap-1 px-2 py-0.5 transition-colors ${
                tab === "preview"
                  ? "bg-surface-alt font-bold text-accent shadow-sm"
                  : "text-subtext hover:text-text"
              }`}
              onClick={() => setTab("preview")}
            >
              <Eye size={11} />
              preview
            </button>
            <button
              className={`flex cursor-pointer items-center gap-1 px-2 py-0.5 transition-colors ${
                tab === "code"
                  ? "bg-surface-alt font-bold text-accent shadow-sm"
                  : "text-subtext hover:text-text"
              }`}
              onClick={() => setTab("code")}
            >
              <Code2 size={11} />
              source
            </button>
          </div>

          {/* Quick Actions */}
          {tab === "preview" && (
            <button
              className="cursor-pointer rounded p-1 text-meta hover:bg-surface-alt hover:text-accent-2"
              onClick={() => setKey((k) => k + 1)}
              title="Reload preview"
            >
              <RefreshCw size={12} />
            </button>
          )}

          <button
            className="cursor-pointer rounded p-1 text-meta hover:bg-surface-alt hover:text-accent-2"
            onClick={handleCopy}
            title="Copy source code"
          >
            {copied ? <Check size={12} className="text-ok" /> : <Copy size={12} />}
          </button>

          <button
            className="cursor-pointer rounded p-1 text-meta hover:bg-surface-alt hover:text-accent-2"
            onClick={handleOpenBrowser}
            title="Open in new window"
          >
            <ExternalLink size={12} />
          </button>

          <button
            className="cursor-pointer rounded p-1 text-meta hover:bg-danger/15 hover:text-danger"
            onClick={onClose}
            title="Close artifact"
          >
            <X size={13} />
          </button>
        </div>
      </div>

      {/* Main Content Area */}
      <div className="relative min-h-0 flex-1 bg-bg">
        {tab === "preview" ? (
          <iframe
            key={key}
            title={artifact.title}
            srcDoc={artifact.content}
            sandbox="allow-scripts allow-modals allow-forms allow-popups"
            className="h-full w-full border-0 bg-white"
          />
        ) : (
          <div className="h-full overflow-auto p-3 font-mono text-[11px] leading-relaxed text-subtext">
            <pre className="whitespace-pre-wrap select-text">
              <code>{artifact.content}</code>
            </pre>
          </div>
        )}
      </div>
    </div>
  );
}
