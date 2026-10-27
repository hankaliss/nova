import { useState } from "react";
import { ChevronDown, LogIn, Plus, RotateCw } from "lucide-react";
import { Btn } from "./Btn";
import { Dialog } from "./Dialog";
import { Switch } from "./Switch";
import { GitHubSetupForm } from "./GitHubSetupForm";
import { CustomServerForm } from "./CustomServerForm";

export interface McpServerStatus {
  name: string;
  enabled: boolean;
  state: string; // disabled | starting | connected | failed
  error: string;
  command: string;
  toolCount: number;
  /** The server's tools with the risk Nova gates them with. */
  tools?: { name: string; description: string; risk: string; skill_only?: boolean }[];
  auth?: { url: string; code: string } | null;
}

const RISK_STYLE: Record<string, { label: string; tone: string }> = {
  read: { label: "Read", tone: "bg-ok/15 text-ok" },
  write: { label: "Asks first", tone: "bg-warn/15 text-warn" },
  destructive: { label: "Always asks", tone: "bg-danger/15 text-danger" },
};

const STATE_DOT: Record<string, string> = {
  connected: "var(--color-ok)",
  starting: "var(--color-warn)",
  failed: "var(--color-danger)",
  disabled: "var(--color-faint)",
};

const STATE_LABEL: Record<string, string> = {
  connected: "Connected",
  starting: "Starting…",
  failed: "Failed to start",
  disabled: "Turned off",
};

export function McpDialog({
  open,
  servers,
  onToggle,
  onRestart,
  onSave,
  onClose,
}: {
  open: boolean;
  servers: McpServerStatus[];
  onToggle: (name: string, enabled: boolean) => void;
  onRestart: (name: string) => void;
  onSave: (name: string, entry: unknown) => Promise<boolean>;
  onClose: () => void;
}) {
  const [addingGitHub, setAddingGitHub] = useState(false);
  const [addingCustom, setAddingCustom] = useState(false);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [advancedOpen, setAdvancedOpen] = useState(false);

  if (!open) return null;

  const hasGithub = servers.some((s) => s.name === "github");

  const toggleExpanded = (name: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      next.has(name) ? next.delete(name) : next.add(name);
      return next;
    });
  };

  const handleSave = async (name: string, entry: unknown) => {
    const ok = await onSave(name, entry);
    if (ok) {
      setAddingGitHub(false);
      setAddingCustom(false);
    }
    return ok;
  };

  return (
    <Dialog title="MCP Servers" onClose={onClose} width={640}>
      <p className="mb-3 text-[12.5px] text-subtext">
        External tool servers Nova can call through the tool registry.
      </p>

      {/* Server list */}
      {!addingGitHub && !addingCustom && (
        <div className="flex flex-col gap-2.5">
          {servers.map((s) => (
            <div
              key={s.name}
              data-mcp-server
              className="rounded-xl border border-border bg-bg/50 p-3.5"
            >
              <div className="flex items-center justify-between">
                <span className="flex min-w-0 items-center gap-2 text-[14px] font-semibold text-text">
                  <span
                    className={`h-2.5 w-2.5 shrink-0 rounded-full ${s.state === "starting" ? "nova-pulse" : ""}`}
                    style={{
                      background: s.auth
                        ? "var(--color-warn)"
                        : (STATE_DOT[s.state] ?? STATE_DOT.disabled),
                    }}
                  />
                  {s.name}
                </span>
                <span className="flex items-center gap-3">
                  {s.auth && (
                    <button
                      className="flex cursor-pointer items-center gap-1 rounded-md px-2 py-1 text-[12.5px] font-medium text-warn hover:bg-warn/10"
                      onClick={() => void window.nova.openAuthUrl?.()}
                      title={`Authorize ${s.name} in your browser`}
                    >
                      <LogIn size={13} aria-hidden="true" />
                      Log in
                    </button>
                  )}
                  {s.state === "failed" && (
                    <button
                      className="flex cursor-pointer items-center gap-1 rounded-md px-2 py-1 text-[12.5px] text-subtext hover:bg-surface-alt hover:text-text"
                      onClick={() => onRestart(s.name)}
                      title="Restart this server"
                    >
                      <RotateCw size={13} aria-hidden="true" />
                      Restart
                    </button>
                  )}
                  <Switch
                    label={`Enable MCP server ${s.name}`}
                    checked={s.enabled}
                    onChange={(v) => onToggle(s.name, v)}
                  />
                </span>
              </div>
              <div className="mt-1.5 truncate font-mono text-[12px] text-meta" title={s.command}>
                {s.command}
              </div>
              <div className="mt-1 text-[13px] text-subtext">
                {s.auth ? (
                  <span className="text-warn">
                    Needs a login. Authorize in your browser, then try again.
                  </span>
                ) : (
                  <>
                    {STATE_LABEL[s.state] ?? s.state}, {s.toolCount} tool{s.toolCount === 1 ? "" : "s"}
                    {s.state === "failed" && s.error && (
                      <span className="mt-0.5 block text-danger">{s.error}</span>
                    )}
                  </>
                )}
              </div>
              {s.toolCount > 0 && (
                <button
                  className="mt-2 flex cursor-pointer items-center gap-1 text-left text-[12.5px] text-subtext hover:text-text"
                  onClick={() => toggleExpanded(s.name)}
                  aria-expanded={expanded.has(s.name)}
                >
                  <ChevronDown size={14} className={`transition-transform ${expanded.has(s.name) ? "rotate-180" : ""}`} aria-hidden="true" />
                  {expanded.has(s.name) ? "Hide tools" : "Show tools"}
                </button>
              )}
              {expanded.has(s.name) && (
                <ul className="mt-2 flex max-h-72 flex-col gap-0.5 overflow-y-auto rounded-lg border border-border bg-bg/60 p-1.5">
                  {(s.tools ?? []).map((t) => {
                    const risk = RISK_STYLE[t.risk] ?? RISK_STYLE.write;
                    return (
                      <li key={t.name} className="rounded-md px-2 py-1.5 hover:bg-surface-alt/60">
                        <div className="flex items-center gap-2">
                          <span className="min-w-0 truncate font-mono text-[12.5px] text-text">
                            {t.name}
                          </span>
                          {t.skill_only && (
                            <span
                              className="ml-auto shrink-0 rounded-full bg-surface-alt px-2 py-0.5 text-[11.5px] text-meta"
                              title="Not sent to the model; skills can use it when it is read-only"
                            >
                              skills only
                            </span>
                          )}
                          <span
                            className={`${t.skill_only ? "" : "ml-auto "}shrink-0 rounded-full px-2 py-0.5 text-[11.5px] font-medium ${risk.tone}`}
                          >
                            {risk.label}
                          </span>
                        </div>
                        {t.description && (
                          <p className="mt-0.5 text-[12.5px] text-meta">{t.description}</p>
                        )}
                      </li>
                    );
                  })}
                  {(s.tools ?? []).length === 0 && (
                    <li className="px-2 py-1.5 text-[12.5px] text-meta">
                      Tool details will appear after the server reconnects.
                    </li>
                  )}
                </ul>
              )}
            </div>
          ))}
          {servers.length === 0 && (
            <div className="rounded-xl border border-dashed border-border p-6 text-center text-[13px] text-meta">
              No servers yet. Add one to give Nova more tools.
            </div>
          )}
        </div>
      )}

      {/* GitHub setup form */}
      {addingGitHub && (
        <GitHubSetupForm onSave={handleSave} onCancel={() => setAddingGitHub(false)} />
      )}

      {/* Custom server setup form */}
      {addingCustom && (
        <CustomServerForm onSave={handleSave} onCancel={() => setAddingCustom(false)} />
      )}

      {/* Bottom actions */}
      {!addingGitHub && !addingCustom && (
        <div className="mt-4 border-t border-border pt-3">
          {/* Primary: add GitHub */}
          {!hasGithub && (
            <Btn
              variant="accent"
              onClick={() => setAddingGitHub(true)}
              className="w-full"
            >
              <Plus size={15} aria-hidden="true" />
              Add GitHub server
            </Btn>
          )}

          {/* Advanced: custom server */}
          <div className="mt-3">
            <button
              className="flex cursor-pointer items-center gap-1 text-[12.5px] text-subtext hover:text-text"
              onClick={() => setAdvancedOpen(!advancedOpen)}
              aria-expanded={advancedOpen}
            >
              <ChevronDown size={14} className={`transition-transform ${advancedOpen ? "rotate-180" : ""}`} aria-hidden="true" />
              Advanced
            </button>
          </div>
          {advancedOpen && (
            <div className="mt-2">
              <Btn
                variant="ghost"
                onClick={() => setAddingCustom(true)}
                className="w-full"
              >
                <Plus size={15} aria-hidden="true" />
                Add custom server
              </Btn>
            </div>
          )}
        </div>
      )}
    </Dialog>
  );
}
