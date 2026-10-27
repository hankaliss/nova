import { useState } from "react";
import { Check, ChevronDown } from "lucide-react";
import { ActivityPhase, ActivityState } from "../lib/types";
import { NovaMark } from "./NovaMark";
import { toolKind } from "../lib/toolKinds";

const PHASE_COLOR: Record<ActivityPhase, string> = {
  idle: "text-meta",
  waiting: "text-meta",
  thinking: "text-subtext",
  generating: "text-accent",
  running: "text-warn",
  approval: "text-warn",
  done: "text-ok",
};

const PHASE_LABEL: Record<ActivityPhase, string> = {
  idle: "",
  waiting: "Waiting for the model",
  thinking: "Reasoning",
  generating: "Writing",
  running: "Running",
  approval: "Waiting for your approval",
  done: "Done",
};

function Indicator({ phase }: { phase: ActivityPhase }) {
  if (phase === "done") return <Check size={15} className="text-ok" aria-hidden="true" />;
  if (phase === "running" || phase === "approval") {
    return <span className="nova-pulse mx-1 h-2 w-2 rounded-full bg-warn" aria-hidden="true" />;
  }
  return <NovaMark size={15} flare />;
}

export function ActivityStrip({
  activity,
  model,
}: {
  activity: ActivityState;
  model: string;
}) {
  const [open, setOpen] = useState(false);
  const { phase, tool, note, tokens, tps, elapsed, steps, summary } = activity;
  if (phase === "idle") return null;

  const displayElapsed =
    phase === "done" ? summary?.elapsed ?? elapsed : elapsed;
  const displayTps =
    phase === "done" ? summary?.tps ?? tps : tps;
  const displayLabel =
    phase === "done" ? summary?.label ?? "done" : note ?? PHASE_LABEL[phase];

  const color = PHASE_COLOR[phase];
  return (
    <div className="msg-in shrink-0 border-y border-border bg-surface/60 px-4 py-1.5" aria-live="polite">
      <div className="mx-auto flex max-w-[780px] items-center gap-2.5 px-2 text-[13px]">
        <Indicator phase={phase} />
        <div className="flex min-w-0 flex-1 items-center gap-2">
          <span className={color}>{displayLabel}</span>
          {tool
            ? (() => {
                const kind = toolKind(tool);
                return (
                  <span className="flex min-w-0 items-center gap-1.5 rounded-md bg-surface-alt px-1.5 py-0.5 text-[12px]">
                    <kind.Icon size={13} className={`shrink-0 ${kind.color}`} aria-label={kind.label} />
                    <span className="truncate font-mono text-warn">{tool}</span>
                  </span>
                );
              })()
            : null}
          {phase === "done" && summary && summary.tools > 0 ? (
            <span className="text-meta">
              {summary.tools} tool{summary.tools === 1 ? "" : "s"} used
            </span>
          ) : null}
        </div>
        <div className="tabular flex shrink-0 items-center gap-3 text-[12px] text-meta">
          {tokens ? <span>{tokens} tokens</span> : null}
          {displayTps > 0 ? <span>{displayTps.toFixed(1)} tok/s</span> : null}
          <span>{displayElapsed.toFixed(0)} s</span>
        </div>
        {steps.length > 1 && (
          <button
            className="flex shrink-0 cursor-pointer items-center gap-1 rounded-md px-1.5 py-0.5 text-[12px] text-meta hover:bg-surface-alt hover:text-text"
            onClick={() => setOpen(!open)}
            aria-expanded={open}
          >
            Steps
            <ChevronDown size={13} className={`transition-transform ${open ? "rotate-180" : ""}`} />
          </button>
        )}
      </div>

      {open && steps.length > 1 && (
        <ol className="mx-auto mt-1.5 max-w-[780px] space-y-0.5 px-2 pb-1">
          {steps.map((s, i) => (
            <li key={i} className="flex items-baseline gap-3 text-[12.5px]">
              <span className="tabular w-10 shrink-0 text-right text-meta">
                {s.t.toFixed(1)} s
              </span>
              {s.kind === "tool" ? (
                <span
                  className={
                    s.status === "fail"
                      ? "text-danger"
                      : s.status === "ok"
                        ? "text-ok"
                        : "text-warn"
                  }
                >
                  <span className="font-mono text-[12px]">{s.label}</span>
                  {s.status === "fail" ? " failed" : s.status === "ok" ? " finished" : " running"}
                  {s.durationMs ? ` in ${s.durationMs} ms` : ""}
                </span>
              ) : (
                <span className={s.kind === "generate" ? "text-subtext" : "text-meta"}>
                  {s.label}
                </span>
              )}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
