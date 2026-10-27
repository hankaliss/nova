import { useState } from "react";
import { Btn } from "./Btn";

export interface CustomFormValues {
  name: string;
  command: string;
  args: string;
  env: string;
  cwd: string;
  timeout: string;
}

const DEFAULTS: CustomFormValues = {
  name: "",
  command: "",
  args: "",
  env: "",
  cwd: "",
  timeout: "120",
};

function valuesToConfig(v: CustomFormValues): Record<string, unknown> {
  const args = v.args.split(/\s+/).filter(Boolean);
  const env: Record<string, string> = {};
  if (v.env.trim()) {
    for (const line of v.env.split("\n")) {
      const eq = line.indexOf("=");
      if (eq > 0) {
        env[line.slice(0, eq).trim()] = line.slice(eq + 1).trim();
      }
    }
  }
  const entry: Record<string, unknown> = {
    command: v.command,
    args,
    env,
    enabled: true,
    timeout_s: Number(v.timeout) || 120,
  };
  if (v.cwd.trim()) entry.cwd = v.cwd.trim();
  return entry;
}

export function CustomServerForm({
  onSave,
  onCancel,
}: {
  onSave: (name: string, entry: unknown) => Promise<boolean>;
  onCancel: () => void;
}) {
  const [values, setValues] = useState<CustomFormValues>({ ...DEFAULTS });
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const set = <K extends keyof CustomFormValues>(key: K, val: CustomFormValues[K]) =>
    setValues((v) => ({ ...v, [key]: val }));

  const save = async () => {
    setError("");
    const name = values.name.trim();
    if (!name || !/^[a-zA-Z0-9_-]+$/.test(name)) {
      setError("Name must be letters/digits/dashes (used as mcp.<name>.<tool>)");
      return;
    }
    if (!values.command.trim()) {
      setError("Command is required");
      return;
    }
    const entry = valuesToConfig(values);
    setLoading(true);
    try {
      const ok = await onSave(name, entry);
      if (!ok) setError("Failed to save — check your settings");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex flex-col gap-3">
      <p className="font-display text-[15px] font-semibold text-text">
        Custom MCP Server
      </p>
      <p className="text-[12.5px] text-subtext">
        Any stdio-based MCP server. Fill in the command and arguments.
      </p>

      <Field label="Server name">
        <input
          value={values.name}
          onChange={(e) => set("name", e.target.value)}
          placeholder="my-server"
          className="w-56 rounded-lg border border-border bg-bg px-2.5 py-1.5 font-mono text-[12.5px] text-text outline-none focus:border-accent"
        />
      </Field>

      <Field label="Command">
        <input
          value={values.command}
          onChange={(e) => set("command", e.target.value)}
          placeholder="/path/to/server"
          className="w-72 rounded-lg border border-border bg-bg px-2.5 py-1.5 font-mono text-[12.5px] text-text outline-none focus:border-accent"
        />
      </Field>

      <Field label="Arguments">
        <input
          value={values.args}
          onChange={(e) => set("args", e.target.value)}
          placeholder="--flag value"
          className="w-72 rounded-lg border border-border bg-bg px-2.5 py-1.5 font-mono text-[12.5px] text-text outline-none focus:border-accent"
        />
      </Field>

      <Field label="Environment">
        <textarea
          value={values.env}
          onChange={(e) => set("env", e.target.value)}
          placeholder={"KEY=value\nOTHER=thing"}
          rows={3}
          className="w-72 resize-y rounded-lg border border-border bg-bg p-2.5 font-mono text-[12.5px] leading-relaxed text-text outline-none focus:border-accent"
        />
      </Field>

      <Field label="Working dir">
        <input
          value={values.cwd}
          onChange={(e) => set("cwd", e.target.value)}
          placeholder="(optional)"
          className="w-72 rounded-lg border border-border bg-bg px-2.5 py-1.5 font-mono text-[12.5px] text-text outline-none focus:border-accent"
        />
      </Field>

      <Field label="Timeout (seconds)">
        <input
          value={values.timeout}
          onChange={(e) => set("timeout", e.target.value)}
          className="w-24 rounded-lg border border-border bg-bg px-2.5 py-1.5 font-mono text-[12.5px] text-text outline-none focus:border-accent"
        />
      </Field>

      {error && (
        <div className="rounded-lg border border-danger/40 bg-danger/10 px-3 py-2 text-[13px] text-danger">
          {error}
        </div>
      )}

      <div className="flex justify-end gap-2">
        <Btn variant="plain" onClick={onCancel}>Cancel</Btn>
        <Btn variant="accent" onClick={() => void save()} disabled={loading}>
          {loading ? "Saving…" : "Save & connect"}
        </Btn>
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-3">
      <label className="w-36 shrink-0 text-right text-[13px] text-subtext">
        {label}
      </label>
      {children}
    </div>
  );
}
