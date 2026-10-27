import { useState } from "react";
import { Btn } from "./Btn";

export type AuthMode = "oauth" | "pat";

export interface GitHubFormValues {
  authMode: AuthMode;
  dockerPath: string;
  image: string;
  port: string;
  pat: string;
  readOnly: boolean;
  timeout: string;
}

const DEFAULTS: GitHubFormValues = {
  authMode: "oauth",
  dockerPath: "docker",
  image: "ghcr.io/github/github-mcp-server",
  port: "8085",
  pat: "",
  readOnly: false,
  timeout: "120",
};

function valuesToConfig(v: GitHubFormValues): Record<string, unknown> {
  const args = ["run", "-i", "--rm"];
  const env: Record<string, string> = {};
  const oauth = v.authMode === "oauth";

  if (oauth) {
    args.push("-p", `127.0.0.1:${v.port}:${v.port}`);
    env.GITHUB_OAUTH_CALLBACK_PORT = v.port;
  } else {
    // PAT mode: token is passed straight through — skip callback port.
    env.GITHUB_PERSONAL_ACCESS_TOKEN = v.pat.trim();
  }

  if (v.readOnly) {
    args.push("--env", "GITHUB_OAUTH_READONLY=1");
    env.GITHUB_OAUTH_READONLY = "1";
  }

  // Pass through the relevant auth env var to the container.
  args.push("-e", oauth ? "GITHUB_OAUTH_CALLBACK_PORT" : "GITHUB_PERSONAL_ACCESS_TOKEN");

  args.push(v.image);

  // The server requires the `stdio` subcommand (ghcr.io/github/github-mcp-server >= 1.10).
  args.push("stdio");

  // AD-016: keep the tool surface small — the full toolset (~44 tools)
  // serializes to ~50KB of schemas, dwarfing the model's context on 8GB VRAM.
  // repos+users+context covers repo listing/search AND get_me (the username
  // the model needs to query the user's own repos).
  args.push("--toolsets", "repos,users,context");

  if (oauth) {
    args.push("--oauth-callback-port", v.port);
  }

  return {
    command: v.dockerPath,
    args,
    env,
    enabled: true,
    timeout_s: Number(v.timeout) || 120,
  };
}

export function GitHubSetupForm({
  onSave,
  onCancel,
}: {
  onSave: (name: string, entry: unknown) => Promise<boolean>;
  onCancel: () => void;
}) {
  const [values, setValues] = useState<GitHubFormValues>({ ...DEFAULTS });
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const set = <K extends keyof GitHubFormValues>(key: K, val: GitHubFormValues[K]) =>
    setValues((v) => ({ ...v, [key]: val }));

  const save = async () => {
    setError("");
    if (values.authMode === "pat" && !values.pat.trim()) {
      setError("Please enter a GitHub Personal Access Token");
      return;
    }
    const entry = valuesToConfig(values);
    setLoading(true);
    try {
      const ok = await onSave("github", entry);
      if (!ok) setError("Failed to save — check your settings");
    } finally {
      setLoading(false);
    }
  };

  const oauth = values.authMode === "oauth";

  return (
    <div className="flex flex-col gap-3">
      <p className="font-display text-[15px] font-semibold text-text">
        GitHub MCP Server
      </p>
      <p className="text-[12.5px] text-subtext">
        Issues, PRs, repos via the official GitHub server.
      </p>

      <div className="flex items-center gap-2">
        <label className="flex items-center gap-1.5 cursor-pointer">
          <input
            type="radio"
            name="authMode"
            checked={oauth}
            onChange={() => set("authMode", "oauth")}
            className="accent-accent"
          />
          <span className="text-[12.5px] text-text">Browser login (OAuth)</span>
        </label>
        <label className="flex items-center gap-1.5 cursor-pointer">
          <input
            type="radio"
            name="authMode"
            checked={!oauth}
            onChange={() => set("authMode", "pat")}
            className="accent-accent"
          />
          <span className="text-[12.5px] text-text">Personal Access Token</span>
        </label>
      </div>

      <Field label="Docker binary">
        <input
          value={values.dockerPath}
          onChange={(e) => set("dockerPath", e.target.value)}
          className="w-56 rounded-lg border border-border bg-bg px-2.5 py-1.5 font-mono text-[12.5px] text-text outline-none focus:border-accent"
        />
      </Field>

      <Field label="Image">
        <input
          value={values.image}
          onChange={(e) => set("image", e.target.value)}
          className="w-72 rounded-lg border border-border bg-bg px-2.5 py-1.5 font-mono text-[12.5px] text-text outline-none focus:border-accent"
        />
      </Field>

      {oauth ? (
        <Field label="Callback port">
          <input
            value={values.port}
            onChange={(e) => set("port", e.target.value)}
            className="w-24 rounded-lg border border-border bg-bg px-2.5 py-1.5 font-mono text-[12.5px] text-text outline-none focus:border-accent"
          />
        </Field>
      ) : (
        <Field label="Token">
          <input
            value={values.pat}
            onChange={(e) => set("pat", e.target.value)}
            type="password"
            placeholder="ghp_… (kept in ~/.config/nova/mcp.json)"
            className="w-80 rounded-lg border border-border bg-bg px-2.5 py-1.5 font-mono text-[12.5px] text-text outline-none focus:border-accent"
          />
        </Field>
      )}

      <Field label="Timeout (seconds)">
        <input
          value={values.timeout}
          onChange={(e) => set("timeout", e.target.value)}
          className="w-24 rounded-lg border border-border bg-bg px-2.5 py-1.5 font-mono text-[12.5px] text-text outline-none focus:border-accent"
        />
      </Field>

      <label className="flex items-center gap-2 cursor-pointer">
        <input
          type="checkbox"
          checked={values.readOnly}
          onChange={(e) => set("readOnly", e.target.checked)}
          className="accent-accent"
        />
        <span className="text-[12.5px] text-subtext">Read-only mode</span>
      </label>

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
