import { useState } from "react";
import { ToolEvent } from "../lib/types";
import { prettyJson } from "../lib/format";

export function ToolCard({ tool }: { tool: ToolEvent }) {
  const [expanded, setExpanded] = useState(false);
  const glyph =
    tool.status === "running" ? "▸" : tool.status === "ok" ? "✓" : "✗";
  return (
    <div className="tool-row flex flex-col gap-0.5">
      <div className="flex items-baseline gap-2 font-mono text-[11px]">
        <span className="text-faint select-none">├─</span>
        <span
          className={`${
            tool.status === "fail"
              ? "text-danger"
              : tool.status === "ok"
                ? "text-ok"
                : "text-warn"
          }`}
        >
          {glyph}
        </span>
        <span className={tool.status === "fail" ? "text-danger" : "text-subtext"}>
          {tool.name}
        </span>
        <span className="text-[10px] text-faint">
          {tool.status === "running"
            ? "running…"
            : tool.durationMs > 0
              ? `${tool.durationMs}ms`
              : ""}
        </span>
        {tool.args && tool.args !== "{}" && (
          <button
            className="cursor-pointer text-[10px] text-meta hover:text-subtext"
            onClick={() => setExpanded(!expanded)}
            title="Toggle arguments"
          >
            [{expanded ? "−" : "+"}]
          </button>
        )}
      </div>
      {expanded && (
        <pre className="ml-[3ch] whitespace-pre-wrap border-l border-border pl-3 font-mono text-[10.5px] text-subtext">
          {prettyJson(tool.args)}
        </pre>
      )}
      {tool.status === "fail" && tool.error && (
        <div className="ml-[3ch] text-[10px] text-danger">{tool.error}</div>
      )}
    </div>
  );
}
