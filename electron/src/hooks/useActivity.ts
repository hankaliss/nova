import { useEffect, useRef, useState } from "react";
import { ActivityState, ActivityStep } from "../lib/types";
import { eventScope } from "../lib/events";

/** Live "what is the model doing" strip, derived from sidecar events. */
export function useActivity() {
  // ---- Live "what is the model doing" state -----------------------------
  const [activity, setActivity] = useState<ActivityState>({
    phase: "idle",
    tokens: 0,
    tps: 0,
    elapsed: 0,
    steps: [],
  });
  // Mutable turn state read by the tick loop below; only committed to React
  // state when the visible snapshot actually changes (avoids re-rendering the
  // whole tree on every streaming token).
  const act = useRef({
    active: false,
    start: 0,
    tokens: 0,
    lastChunk: 0,
    window: [] as number[], // timestamps of recent chunks (tok/s window)
    running: null as string | null,
    approval: null as string | null,
    statusLabel: "",
    note: "",
    steps: [] as ActivityStep[],
    genTokens: 0, // tokens accumulated since the last tool call
    summary: undefined as ActivityState["summary"],
    doneAt: 0,
    fade: 0 as number,
    rev: 0, // bumped whenever steps/summary change
  });

  // ---- Activity engine: derive "what is the model doing" from events -----
  useEffect(() => {
    const { on, off } = eventScope();

    const t = () => (Date.now() - act.current.start) / 1000;
    const bump = () => {
      act.current.rev++;
    };

    const closeGen = () => {
      const a = act.current;
      if (a.genTokens > 0) {
        a.steps.push({
          kind: "generate",
          t: t(),
          label: `generated ${a.genTokens} tokens`,
        });
        a.genTokens = 0;
        bump();
      }
    };

    const beginTurn = () => {
      const a = act.current;
      window.clearTimeout(a.fade);
      a.active = true;
      a.start = Date.now();
      a.tokens = 0;
      a.lastChunk = 0;
      a.window = [];
      a.running = null;
      a.approval = null;
      a.statusLabel = "";
      a.note = "";
      a.genTokens = 0;
      a.summary = undefined;
      a.doneAt = 0;
      a.steps = [{ kind: "start", t: 0, label: "turn started" }];
      bump();
      setActivity((prev) => ({
        phase: "waiting",
        tokens: 0,
        tps: 0,
        elapsed: 0,
        steps: [...a.steps],
        summary: undefined,
      }));
    };

    const finalize = () => {
      const a = act.current;
      if (!a.active) return;
      a.active = false;
      a.running = null;
      a.approval = null;
      closeGen();
      const tools = a.steps.filter((s) => s.kind === "tool").length;
      const stopped = /^Generation stopped$/.test(a.statusLabel);
      const failed = a.statusLabel.startsWith("Error");
      const label = failed
        ? a.statusLabel.replace(/^Error:\s*/, "error")
        : stopped
          ? "stopped"
          : "completed";
      a.summary = {
        label,
        tokens: a.tokens,
        tps: 0,
        tools,
        elapsed: Math.max(0.2, (Date.now() - a.start) / 1000),
      };
      a.doneAt = Date.now();
      bump();
      setActivity((prev) => ({
        ...prev,
        phase: "done",
        tool: undefined,
        steps: [...a.steps],
        summary: a.summary,
      }));
      a.fade = window.setTimeout(() => {
        setActivity((prev) => (prev.phase === "done" ? { ...prev, phase: "idle" } : prev));
      }, 10000);
    };

    on("streaming", (v: boolean) => {
      if (v) beginTurn();
      else finalize();
    });

    on("chunk", () => {
      const a = act.current;
      if (!a.active) return;
      const now = Date.now();
      a.tokens += 1;
      a.genTokens += 1;
      a.lastChunk = now;
      a.window.push(now);
      while (a.window.length > 1 && now - a.window[0] > 3000) a.window.shift();
    });

    on("tool_progress", (d) => {
      const a = act.current;
      if (!a.active || !d) return;
      const name = String(d.name ?? "");
      if (d.status === "running") {
        closeGen();
        a.running = name;
        a.approval = null;
        a.steps.push({ kind: "tool", t: t(), label: name, status: "running" });
        bump();
      } else {
        a.running = null;
        for (let i = a.steps.length - 1; i >= 0; i--) {
          if (a.steps[i].kind === "tool") {
            a.steps[i].status = d.status;
            if (d.duration_ms) a.steps[i].durationMs = d.duration_ms;
            if (d.error) a.steps[i].detail = String(d.error);
            break;
          }
        }
        bump();
      }
    });

    // Waiting for the user to grant/deny a tool (the PermissionDialog).
    on("permission", (d) => {
      const a = act.current;
      if (!a.active || !d) return;
      a.approval = String(d.tool ?? "");
      bump();
    });

    // Keep the error/cancel text so the "done" phase says stopped/error.
    // The sidecar explains a wait before the first token (activity_note).
    on("activity_note", (text) => {
      const a = act.current;
      if (!a.active || typeof text !== "string") return;
      a.note = text;
      a.rev++;
    });

    on("statusChanged", (text) => {
      const a = act.current;
      if (!a.active || typeof text !== "string") return;
      a.statusLabel = text;
    });

    // Final real stats arrive right after streamingChanged(false).
    on("response", (d) => {
      const a = act.current;
      const freshDone =
        !a.active && a.doneAt > 0 && Date.now() - a.doneAt <= 5000;
      if (!a.active && !freshDone) return;
      const tokens = Number(d.tokens) || a.tokens;
      const evalNs = Number(d.eval_ns);
      a.summary = {
        ...(a.summary ?? { label: "completed", tokens, tps: 0, tools: 0, elapsed: 0 }),
        tokens,
        tps: evalNs > 0 ? Number(tokens) / (evalNs / 1e9) : 0,
        tools: Number(d.tools) || 0,
        elapsed: Number(d.total_ns) > 0 ? Number(d.total_ns) / 1e9 : a.summary?.elapsed ?? 0,
      };
      bump();
    });

    return () => {
      off();
      window.clearTimeout(act.current.fade);
    };
  }, []);

  // Commit the live snapshot ~4x/sec, only when something visible changed.
  useEffect(() => {
    let last = "";
    const id = window.setInterval(() => {
      const a = act.current;
      const now = Date.now();
      let phase: ActivityState["phase"] = "idle";
      let tool: string | undefined;
      let tps = 0;

      if (a.active) {
        if (a.running) {
          phase = "running";
          tool = a.running;
        } else if (a.approval) {
          phase = "approval";
          tool = a.approval;
        } else if (a.tokens === 0) {
          phase = "waiting";
        } else if (now - a.lastChunk <= 1400) {
          phase = "generating";
          const spanS = (now - a.window[0]) / 1000;
          tps = spanS > 0.4 ? a.window.length / spanS : 0;
        } else {
          phase = "thinking";
        }
      } else if (a.doneAt && now - a.doneAt < 10000) {
        phase = "done";
      }

      const key = `${phase}|${tool ?? ""}|${a.tokens}|${Math.round(tps * 10)}|${Math.floor((now - a.start) / 1000)}|${a.rev}`;
      if (key === last) return;
      last = key;
      const note = phase === "waiting" ? a.note || undefined : undefined;
      setActivity((prev) => ({ ...prev, phase, tool, note, tokens: a.tokens, tps, steps: a.steps, summary: a.summary }));
    }, 250);
    return () => window.clearInterval(id);
  }, []);

  return activity;
}
