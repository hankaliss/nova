import { Dialog } from "./Dialog";
import { Switch } from "./Switch";

export function SkillsDialog({
  open,
  skills,
  onToggle,
  onClose,
}: {
  open: boolean;
  skills: any[];
  onToggle: (name: string, enabled: boolean) => void;
  onClose: () => void;
}) {
  if (!open) return null;
  return (
    <Dialog title="Skills" onClose={onClose} width={580}>
      <p className="mb-3 text-xs text-subtext">
        Skills add tools Nova can use in conversation. Enable a skill and the
        assistant can call its tools automatically when relevant — the usual
        permission rules still apply.
      </p>
      <div className="flex flex-col gap-2.5">
        {skills.map((s) => (
          <div
            key={s.name}
            className="flex items-start gap-3 rounded-[10px] border border-border bg-bg p-3"
          >
            <div className="min-w-0 flex-1">
              <div className="flex items-center justify-between">
                <span className="truncate text-sm font-bold text-accent">
                  {s.name}
                </span>
                <Switch
                  checked={s.enabled}
                  onChange={(v) => onToggle(s.name, v)}
                />
              </div>
              <div className="mt-0.5 text-xs text-text">
                {s.description}
              </div>
              {s.tools.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {s.tools.map((t: any, i: number) => (
                    <span
                      key={i}
                      className="flex items-center gap-1.5 rounded-md bg-surface-alt px-2 py-0.5 text-[10px] text-subtext"
                    >
                      <span
                        className="h-2 w-2 rounded-full"
                        style={{
                          background:
                            t.risk === "read"
                              ? "var(--color-ok)"
                              : t.risk === "write"
                                ? "var(--color-warn)"
                                : "var(--color-danger)",
                        }}
                      />
                      {t.name}
                    </span>
                  ))}
                </div>
              )}
            </div>
          </div>
        ))}
      </div>
    </Dialog>
  );
}