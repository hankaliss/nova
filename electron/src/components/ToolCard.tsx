import { useState } from "react";
import { Check, ChevronDown, LoaderCircle, X } from "lucide-react";
import { ToolEvent } from "../lib/types";
import { toolKind } from "../lib/toolKinds";
import { prettyJson } from "../lib/format";

export function ToolCard({ tool }: { tool: ToolEvent }) {
  const [expanded, setExpanded] = useState(false);
  const hasArgs = !!tool.args && tool.args !== "{}";
  const kind = toolKind(tool.name);
  const status =
    tool.status === "running"
      ? { icon: <LoaderCircle size={14} className="animate-spin text-warn" />, label: "Running" }
      : tool.status === "ok"
        ? { icon: <Check size={14} className="text-ok" />, label: "Done" }
        : { icon: <X size={14} className="text-danger" />, label: "Failed" };
  return (
    <div className="tool-row w-fit max-w-full rounded-lg border border-border bg-surface/70">
      <button
        className={`flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-[12.5px] ${
          hasArgs ? "cursor-pointer" : "cursor-default"
        }`}
        onClick={() => hasArgs && setExpanded(!expanded)}
        aria-expanded={hasArgs ? expanded : undefined}
        title={hasArgs ? "Show the arguments Nova sent" : undefined}
      >
        {/* What kind of tool ran: web, a skill, the shell, ... */}
        <span className={`flex shrink-0 items-center gap-1 text-[12px] font-medium ${kind.color}`}>
          <kind.Icon size={13} aria-hidden="true" />
          {kind.label}
        </span>
        <span
          className={`truncate font-mono text-[12px] ${
            tool.status === "fail" ? "text-danger" : "text-subtext"
          }`}
        >
          {tool.name}
        </span>
        <span className="flex shrink-0 items-center gap-1" aria-label={status.label}>
          {status.icon}
        </span>
        {tool.status !== "running" && tool.durationMs > 0 && (
          <span className="tabular shrink-0 text-[12px] text-meta">
            {tool.durationMs < 1000
              ? `${tool.durationMs} ms`
              : `${(tool.durationMs / 1000).toFixed(1)} s`}
          </span>
        )}
        {hasArgs && (
          <ChevronDown
            size={14}
            className={`shrink-0 text-meta transition-transform ${expanded ? "rotate-180" : ""}`}
            aria-hidden="true"
          />
        )}
      </button>
      {expanded && (
        <pre tabIndex={0} className="max-h-60 overflow-auto whitespace-pre-wrap border-t border-border px-3 py-2 font-mono text-[12px] text-subtext">
          {prettyJson(tool.args)}
        </pre>
      )}
      {tool.status === "fail" && tool.error && (
        <div className="border-t border-border px-3 py-2 text-[12.5px] text-danger">
          {tool.error}
        </div>
      )}
    </div>
  );
}
