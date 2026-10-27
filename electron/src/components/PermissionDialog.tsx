import { useState, useEffect } from "react";
import { Dialog } from "./Dialog";
import { Btn } from "./Btn";
import { Switch } from "./Switch";
import { prettyJson } from "../lib/format";

export function PermissionDialog({
  request,
  onRespond,
}: {
  request: {
    tool: string;
    risk: string;
    permission: string;
    args: string;
  } | null;
  onRespond: (approved: boolean, remember: boolean) => void;
}) {
  const [remember, setRemember] = useState(false);
  useEffect(() => setRemember(false), [request]);
  if (!request) return null;
  const risk =
    request.risk === "destructive"
      ? {
          tone: "border-danger/50 bg-danger/10 text-danger",
          title: "This can't be undone",
          body: "Nova wants to run something that deletes data or runs new code.",
        }
      : request.risk === "write"
        ? {
            tone: "border-warn/50 bg-warn/10 text-warn",
            title: "This changes your system",
            body: "Nova wants to run something that modifies files, settings or software.",
          }
        : {
            tone: "border-ok/40 bg-ok/10 text-ok",
            title: "Read only",
            body: "This only reads information.",
          };

  // DESTRUCTIVE tools always ask — the backend never stores a grant for them.
  const canRemember = request.risk !== "destructive";
  const isSkill = request.tool.startsWith("nova.skill.");
  let skillPreview: {
    name: string;
    files: { file: string; lines: number }[];
    code: string;
  } | null = null;
  if (request.args && request.args !== "{}") {
    try {
      const a = JSON.parse(request.args);
      if (isSkill && typeof a.name === "string") {
        const files: { file: string; lines: number }[] = [];
        if (typeof a.skill_md === "string" && a.skill_md.trim())
          files.push({
            file: "SKILL.md",
            lines: a.skill_md.trim().split("\n").length,
          });
        if (typeof a.tools_py === "string" && a.tools_py.trim())
          files.push({
            file: "tools.py",
            lines: a.tools_py.trim().split("\n").length,
          });
        skillPreview = {
          name: a.name,
          files,
          code: typeof a.tools_py === "string" ? a.tools_py.trim() : "",
        };
      }
    } catch {
      skillPreview = null;
    }
  }
  const actionLabel =
    request.tool === "nova.skill.create"
      ? "Create a new skill"
      : request.tool === "nova.skill.update"
        ? "Update an existing skill"
        : request.tool === "nova.skill.delete"
          ? "Delete a skill"
          : null;
  return (
    <Dialog
      title="Allow this action?"
      onClose={() => onRespond(false, false)}
      width={540}
      footer={
        <>
          <Btn variant="ghost" initialFocus onClick={() => onRespond(false, false)}>
            Deny
          </Btn>
          <Btn variant="accent" onClick={() => onRespond(true, canRemember && remember)}>
            Approve
          </Btn>
        </>
      }
    >
      <div className={`mb-4 rounded-xl border px-3.5 py-2.5 ${risk.tone}`}>
        <p className="text-[14px] font-semibold">{risk.title}</p>
        <p className="mt-0.5 text-[13px] opacity-90">{risk.body}</p>
      </div>
      <p className="text-[13px] text-subtext">Tool</p>
      <p className="mb-3 truncate font-mono text-[14px] text-text">{request.tool}</p>
      {actionLabel && (
        <div className="mb-3 rounded-xl bg-surface-alt px-3.5 py-2.5">
          <div className="text-[13px] font-semibold text-text">
            {actionLabel}
          </div>
          {skillPreview && (
            <div className="mt-1 flex items-baseline gap-2 text-sm">
              <span className="font-mono font-semibold text-accent">{skillPreview.name}</span>
              <span className="text-[12.5px] text-subtext">
                {skillPreview.files.length === 0
                  ? "no file changes (update details only)"
                  : skillPreview.files
                      .map((f) => `${f.file} (${f.lines} lines)`)
                      .join(", ")}
              </span>
            </div>
          )}
        </div>
      )}
      {skillPreview?.code && (
        <>
          <p className="mb-1.5 text-[13px] text-subtext">
            The code below will run inside Nova with your user's permissions.
          </p>
          <pre tabIndex={0} className="mb-2 max-h-[260px] overflow-auto rounded-xl border border-border bg-code p-3 font-mono text-[12.5px] text-text">
            {skillPreview.code}
          </pre>
        </>
      )}
      {!skillPreview && request.args && request.args !== "{}" && (
        <>
          <p className="mb-1.5 text-[13px] text-subtext">What it will run with</p>
          <pre tabIndex={0} className="max-h-[200px] overflow-auto rounded-xl border border-border bg-code p-3 font-mono text-[12.5px] text-accent-2">
            {prettyJson(request.args)}
          </pre>
        </>
      )}
      {canRemember && (
        <label className="mt-4 flex cursor-pointer items-center gap-2.5 text-[13px] text-subtext">
          <Switch checked={remember} onChange={setRemember} label="Always allow this tool" />
          Always allow this tool
        </label>
      )}
    </Dialog>
  );
}