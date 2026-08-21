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
  const riskColor =
    request.risk === "destructive"
      ? "var(--color-danger)"
      : "var(--color-warn)";
  const riskLabel =
    request.risk === "destructive"
      ? "DESTRUCTIVE — irreversible action"
      : request.risk === "write"
        ? "WRITE — modifies your system"
        : "READ — safe to run";
  return (
    <Dialog
      title="Tool permission required"
      onClose={() => onRespond(false, remember)}
      width={540}
      footer={
        <>
          <Btn variant="ghost" onClick={() => onRespond(false, remember)}>
            Deny
          </Btn>
          <Btn variant="accent" onClick={() => onRespond(true, remember)}>
            Approve
          </Btn>
        </>
      }
    >
      <div
        className="mb-3 flex h-8 items-center justify-center rounded-md text-[9px] font-bold tracking-[1px]"
        style={{ background: riskColor, color: "#11111b" }}
      >
        {riskLabel.toUpperCase()}
      </div>
      <div className="text-xs text-subtext">Nova wants to run this tool:</div>
      <div className="mb-1 truncate text-[15px] font-bold text-accent">
        {request.tool}
      </div>
      {request.permission && (
        <div className="mb-2 text-xs text-meta">
          Permission: {request.permission}
        </div>
      )}
      {request.args && request.args !== "{}" && (
        <>
          <div className="mb-1 text-xs text-subtext">Arguments:</div>
          <pre className="max-h-[180px] overflow-auto rounded-md bg-bg p-2 font-mono text-[10.5px] text-warn">
            {prettyJson(request.args)}
          </pre>
        </>
      )}
      <label className="mt-3 flex cursor-pointer items-center gap-2 text-xs text-subtext">
        <Switch checked={remember} onChange={setRemember} />
        Always allow this tool
      </label>
    </Dialog>
  );
}