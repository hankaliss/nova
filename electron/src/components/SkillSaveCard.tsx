import { useEffect, useRef, useState } from "react";
import { alignToolNames, DetectedSkill } from "../lib/skillDetect";
import { Check } from "lucide-react";
import { NovaMark } from "./NovaMark";

type Status = "idle" | "saving" | "ok" | "warn" | "error";
const SMOKE = "SMOKE TEST FAILED";

/**
 * "Save skill" fallback card: the LLM showed SKILL.md + tools.py (or shell
 * command to create them) but never called nova.skill.create. The user clicks
 * Save — which calls the createSkill RPC (same engine as the tool) so the
 * files land on disk. The backend returns a summary that may report READ
 * smoke-test failures; those stay visible instead of silently "saving".
 */
export function SkillSaveCard({
  skill,
  onSaved,
  onFix,
}: {
  skill: DetectedSkill;
  onSaved: (name: string) => void;
  onFix?: (name: string, detail: string) => void;
}) {
  const [name, setName] = useState(skill.name);
  const [status, setStatus] = useState<Status>("idle");
  const [result, setResult] = useState("");
  const [errorMsg, setErrorMsg] = useState("");
  const [fixing, setFixing] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (status === "ok") {
      const t = setTimeout(() => onSaved(name), 1600);
      return () => clearTimeout(t);
    }
  }, [status, name, onSaved]);

  const validName = /^[a-z][a-z0-9_]*$/.test(name);
  const finished = status === "ok" || status === "warn";

  const save = async () => {
    if (!validName || status === "saving" || finished) return;
    setStatus("saving");
    setErrorMsg("");
    setResult("");
    try {
      const toolsPy = alignToolNames(skill.tools_py, name);
      const summary = (await window.nova.call("createSkill", {
        name,
        skill_md: skill.skill_md,
        tools_py: toolsPy,
      })) as unknown as string;
      if (summary.includes(SMOKE)) {
        setStatus("warn");
      } else {
        setStatus("ok");
      }
      setResult(summary);
    } catch (e) {
      setStatus("error");
      setErrorMsg(e instanceof Error ? e.message : String(e));
    }
  };

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") save();
  };

  const warnDetail = result.includes(SMOKE)
    ? result.split(SMOKE)[1]?.replace(/^[\s:—-]*/, "") || ""
    : "";

  return (
    <div className="msg-in mb-6 flex gap-3">
      <div className="pt-[3px]">
        <NovaMark size={18} />
      </div>
      <div className="min-w-0 flex-1 rounded-xl border border-border bg-surface p-4">
        <p className="text-[14.5px] font-semibold text-text">Save this as a skill</p>
        <p className="mt-0.5 flex items-center gap-3 text-[12.5px] text-meta">
          <span className="flex items-center gap-1 text-ok">
            <Check size={13} aria-hidden="true" /> SKILL.md
          </span>
          <span className="flex items-center gap-1 text-ok">
            <Check size={13} aria-hidden="true" /> tools.py
          </span>
          <span>found in the answer above</span>
        </p>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <label className="text-[13px] text-subtext" htmlFor="skill-name">
            Name
          </label>
          <input
            id="skill-name"
            ref={inputRef}
            className={`w-64 rounded-lg border bg-bg px-2.5 py-1.5 font-mono text-[13px] text-text outline-none transition-colors ${
              validName ? "border-border focus:border-accent" : "border-danger/60 focus:border-danger"
            }`}
            value={name}
            disabled={status === "saving" || finished}
            onChange={(e) => {
              setName(e.target.value);
              if (status === "error") setStatus("idle");
            }}
            onKeyDown={onKey}
            placeholder="skill_name"
            spellCheck={false}
            aria-invalid={!validName}
          />
          {!validName && (
            <span className="text-[12.5px] text-danger">
              Use lowercase letters, digits and underscores.
            </span>
          )}
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-2.5">
          <button
            className={`flex h-8 cursor-pointer items-center gap-1.5 rounded-lg px-3.5 text-[13px] font-semibold transition-colors disabled:cursor-default disabled:opacity-60 ${
              status === "ok"
                ? "bg-ok text-bg"
                : status === "warn"
                  ? "bg-warn text-bg"
                  : "bg-accent text-accent-text hover:bg-accent-2"
            }`}
            disabled={!validName || status === "saving" || finished}
            onClick={save}
          >
            {status === "saving"
              ? "Saving…"
              : status === "ok"
                ? "Saved"
                : status === "warn"
                  ? "Saved, needs fixes"
                  : "Save skill"}
          </button>
          {status === "error" && (
            <span role="alert" className="min-w-0 break-all text-[12.5px] text-danger">
              {errorMsg}
            </span>
          )}
          {status === "ok" && (
            <span className="text-[12.5px] text-ok">It's live in the Skills menu.</span>
          )}
          {status === "warn" && (
            <span className="min-w-0 text-[12.5px] text-warn">
              Saved, but its read test failed: {warnDetail}
            </span>
          )}
          {status === "warn" && onFix && (
            <button
              className="flex h-8 cursor-pointer items-center rounded-lg border border-warn/70 px-3 text-[13px] text-warn transition-colors hover:bg-warn/10 disabled:cursor-default disabled:opacity-50"
              disabled={fixing}
              onClick={() => {
                setFixing(true);
                onFix(name, warnDetail);
              }}
            >
              {fixing ? "Fix requested…" : "Ask Nova to fix it"}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
