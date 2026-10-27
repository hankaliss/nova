import { useState } from "react";
import {
  AppWindow,
  Check,
  ChevronLeft,
  ChevronRight,
  Code2,
  Copy,
  ExternalLink,
  Eye,
  RefreshCw,
  X,
} from "lucide-react";
import { Artifact, displayTitle } from "../lib/artifacts";

export function ArtifactPanel({
  artifact,
  versions,
  onSelect,
  onClose,
}: {
  artifact: Artifact;
  /** All revisions of this artifact, oldest first. */
  versions: Artifact[];
  onSelect: (artifact: Artifact) => void;
  onClose: () => void;
}) {
  const at = versions.findIndex((v) => v.id === artifact.id);
  const prev = at > 0 ? versions[at - 1] : null;
  const next = at >= 0 && at < versions.length - 1 ? versions[at + 1] : null;

  const [tab, setTab] = useState<"preview" | "code">("preview");
  const [copied, setCopied] = useState(false);
  const [key, setKey] = useState(0);

  const handleCopy = () => {
    void window.nova.copyText(artifact.content);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  // Opens in the system browser via the main process: a Nova window would
  // give the model-written page access to Nova's backend.
  const handleOpenBrowser = () => {
    void window.nova.openArtifact?.(artifact.content);
  };

  return (
    <aside
      aria-label="Artifact preview"
      className="@container flex h-full w-[min(480px,45%)] min-w-[320px] shrink-0 flex-col border-l border-border bg-surface"
    >
      {/* Top Header */}
      {/* Wraps onto a second row when the panel is too narrow for all of it */}
      <div className="flex min-h-12 shrink-0 flex-wrap items-center justify-between gap-x-2 gap-y-1 border-b border-border px-3 py-1.5">
        <div className="flex min-w-[80px] flex-1 items-center gap-2">
          <AppWindow size={16} className="shrink-0 text-accent" aria-hidden="true" />
          <span className="truncate text-[14px] font-semibold text-text" title={artifact.title}>
            {displayTitle(artifact)}
          </span>
        </div>

        {/* Revisions of this artifact */}
        {versions.length > 1 && (
          <div className="flex shrink-0 items-center text-[12.5px] text-subtext">
            <button
              className="cursor-pointer rounded-md p-1 text-meta hover:bg-surface-alt hover:text-text disabled:cursor-default disabled:opacity-40 disabled:hover:bg-transparent"
              onClick={() => prev && onSelect(prev)}
              disabled={!prev}
              title="Previous version"
              aria-label="Previous version"
            >
              <ChevronLeft size={15} />
            </button>
            <span className="tabular px-0.5" aria-live="polite">
              v{artifact.version} of {versions.length}
            </span>
            <button
              className="cursor-pointer rounded-md p-1 text-meta hover:bg-surface-alt hover:text-text disabled:cursor-default disabled:opacity-40 disabled:hover:bg-transparent"
              onClick={() => next && onSelect(next)}
              disabled={!next}
              title="Next version"
              aria-label="Next version"
            >
              <ChevronRight size={15} />
            </button>
          </div>
        )}

        {/* View Switcher Tabs */}
        <div className="flex shrink-0 items-center gap-1">
          <div role="tablist" className="flex rounded-lg bg-bg p-0.5 text-[12.5px]">
            <button
              role="tab"
              aria-selected={tab === "preview"}
              className={`flex cursor-pointer items-center gap-1.5 rounded-md px-2.5 py-1 transition-colors ${
                tab === "preview"
                  ? "bg-surface-alt font-semibold text-text"
                  : "text-meta hover:text-text"
              }`}
              onClick={() => setTab("preview")}
              title="Preview"
              aria-label="Preview"
            >
              <Eye size={13} />
              {/* Labels only when the panel is wide enough for every button. */}
              <span className="hidden @md:inline">Preview</span>
            </button>
            <button
              role="tab"
              aria-selected={tab === "code"}
              className={`flex cursor-pointer items-center gap-1.5 rounded-md px-2.5 py-1 transition-colors ${
                tab === "code"
                  ? "bg-surface-alt font-semibold text-text"
                  : "text-meta hover:text-text"
              }`}
              onClick={() => setTab("code")}
              title="Code"
              aria-label="Code"
            >
              <Code2 size={13} />
              <span className="hidden @md:inline">Code</span>
            </button>
          </div>

          {/* Quick Actions */}
          {tab === "preview" && (
            <button
              className="cursor-pointer rounded-lg p-1.5 text-meta hover:bg-surface-alt hover:text-text"
              onClick={() => setKey((k) => k + 1)}
              title="Reload preview"
              aria-label="Reload preview"
            >
              <RefreshCw size={15} />
            </button>
          )}

          <button
            className="cursor-pointer rounded-lg p-1.5 text-meta hover:bg-surface-alt hover:text-text"
            onClick={handleCopy}
            title="Copy source code"
            aria-label="Copy source code"
          >
            {copied ? <Check size={15} className="text-ok" /> : <Copy size={15} />}
          </button>

          <button
            className="cursor-pointer rounded-lg p-1.5 text-meta hover:bg-surface-alt hover:text-text"
            onClick={handleOpenBrowser}
            title="Open in your browser"
            aria-label="Open in your browser"
          >
            <ExternalLink size={15} />
          </button>

          <button
            className="cursor-pointer rounded-lg p-1.5 text-meta hover:bg-surface-alt hover:text-text"
            onClick={onClose}
            title="Close artifact"
            aria-label="Close artifact"
          >
            <X size={16} />
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
          <div className="h-full overflow-auto p-3 font-mono text-[12.5px] leading-relaxed text-subtext">
            <pre tabIndex={0} className="whitespace-pre-wrap select-text">
              <code>{artifact.content}</code>
            </pre>
          </div>
        )}
      </div>
    </aside>
  );
}
