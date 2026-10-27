import { useEffect, useState } from "react";
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

const WIDTH_KEY = "nova.artifactWidth";
const MIN_WIDTH = 320;
/** Room the chat column keeps however wide the panel gets. */
const CHAT_MIN = 360;
const KEY_STEP = 32;

function defaultWidth(): number {
  return Math.min(480, Math.round(window.innerWidth * 0.45));
}

function clampWidth(width: number): number {
  const max = Math.max(MIN_WIDTH, window.innerWidth - CHAT_MIN);
  return Math.round(Math.min(Math.max(width, MIN_WIDTH), max));
}

function storedWidth(): number | null {
  try {
    const value = Number(localStorage.getItem(WIDTH_KEY));
    return value > 0 ? value : null;
  } catch {
    return null;
  }
}

function saveWidth(width: number | null) {
  try {
    if (width === null) localStorage.removeItem(WIDTH_KEY);
    else localStorage.setItem(WIDTH_KEY, String(width));
  } catch {
    /* width just isn't remembered */
  }
}

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
  // null = default width (follows the window); a number = the user's choice.
  const [chosen, setChosen] = useState<number | null>(storedWidth);
  const [dragging, setDragging] = useState(false);
  const [, setWindowWidth] = useState(window.innerWidth);
  const width = clampWidth(chosen ?? defaultWidth());

  // Re-clamp when the window shrinks, so the chat column keeps its room.
  useEffect(() => {
    const onResize = () => setWindowWidth(window.innerWidth);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  const choose = (value: number | null) => {
    const next = value === null ? null : clampWidth(value);
    setChosen(next);
    saveWidth(next);
  };

  // The panel is on the right: dragging the left edge leftwards widens it.
  const handlePointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return;
    e.preventDefault();
    const startX = e.clientX;
    const startWidth = width;
    const handle = e.currentTarget;
    handle.setPointerCapture?.(e.pointerId);
    setDragging(true);
    const onMove = (ev: PointerEvent) => setChosen(clampWidth(startWidth + startX - ev.clientX));
    const onUp = (ev: PointerEvent) => {
      handle.removeEventListener("pointermove", onMove);
      handle.removeEventListener("pointerup", onUp);
      handle.removeEventListener("pointercancel", onUp);
      setDragging(false);
      choose(startWidth + startX - ev.clientX);
    };
    handle.addEventListener("pointermove", onMove);
    handle.addEventListener("pointerup", onUp);
    handle.addEventListener("pointercancel", onUp);
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (e.key === "ArrowLeft") choose(width + KEY_STEP);
    else if (e.key === "ArrowRight") choose(width - KEY_STEP);
    else if (e.key === "Home") choose(null);
    else return;
    e.preventDefault();
  };

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
      style={{ width }}
      className="@container relative flex h-full shrink-0 flex-col border-l border-border bg-surface"
    >
      {/* Resize handle on the left edge; double-click resets the width */}
      <div
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize artifact panel"
        aria-valuenow={width}
        aria-valuemin={MIN_WIDTH}
        tabIndex={0}
        title="Drag to resize · double-click to reset"
        onPointerDown={handlePointerDown}
        onDoubleClick={() => choose(null)}
        onKeyDown={handleKeyDown}
        className={`absolute inset-y-0 -left-1 z-10 w-2 cursor-col-resize transition-colors hover:bg-accent/40 focus-visible:bg-accent/60 focus-visible:outline-none ${
          dragging ? "bg-accent/60" : ""
        }`}
      />
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
            // While dragging, the page must not swallow the pointer.
            className={`h-full w-full border-0 bg-white ${dragging ? "pointer-events-none" : ""}`}
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
