import { useCallback, useEffect, useRef, useState } from "react";
import { Check, Copy, Cpu, Download, Globe, RefreshCw, ShieldCheck } from "lucide-react";
import { Dialog } from "./Dialog";
import { Btn } from "./Btn";
import { ShortcutField, ShortcutStatus } from "./ShortcutField";
import { eventScope } from "../lib/events";
import { Settings } from "../lib/types";

/** `getSetupStatus` result (src/nova/rpc/setup.py). */
export interface SetupStatus {
  ollama: { installed: boolean; reachable: boolean };
  gpu: { name: string; vram_total_mib: number } | null;
  models: string[];
  recommended: { model: string; note: string };
  searxng: boolean;
  shortcut: string;
  onboarding_done: boolean;
  pulling: boolean;
}

interface PullProgress {
  model: string;
  status: string;
  completed: number;
  total: number;
}

const STEPS = ["Ollama", "Model", "Extras", "Ready"];
const POLL_MS = 2000;
const INSTALL_CMD = "curl -fsSL https://ollama.com/install.sh | sh";
const START_CMD = "sudo systemctl enable --now ollama";

function gb(bytes: number): string {
  return `${(bytes / 1e9).toFixed(bytes >= 1e10 ? 0 : 1)} GB`;
}

/**
 * First-run setup (issue #20, AD-040): Ollama → model → extras → ready.
 * Every step can be skipped; closing counts as done. Nova never runs sudo:
 * commands are shown with a copy button and the step re-checks by itself.
 */
export function SetupWizard({
  open,
  settings,
  onSaveSettings,
  onClose,
}: {
  open: boolean;
  settings: Settings | null;
  onSaveSettings: (settings: Settings) => void;
  onClose: () => void;
}) {
  const [status, setStatus] = useState<SetupStatus | null>(null);
  const [step, setStep] = useState(0);
  const [pull, setPull] = useState<PullProgress | null>(null);
  const [pullError, setPullError] = useState<{ model: string; error: string } | null>(null);
  const [selected, setSelected] = useState("");
  const [shortcut, setShortcut] = useState("");
  const [shortcutStatus, setShortcutStatus] = useState<ShortcutStatus | null>(null);
  const [copied, setCopied] = useState("");
  const stepRef = useRef(step);
  stepRef.current = step;

  const refresh = useCallback(async (): Promise<SetupStatus | null> => {
    try {
      const s = (await window.nova.call("getSetupStatus")) as SetupStatus;
      if (s) setStatus(s);
      return s ?? null;
    } catch {
      return null;
    }
  }, []);

  // Fresh state each time it opens; start at the first step that needs work.
  useEffect(() => {
    if (!open) return;
    setPull(null);
    setPullError(null);
    setSelected("");
    setShortcut(settings?.toggle_shortcut ?? "");
    void refresh().then((s) => setStep(s?.ollama.reachable ? 1 : 0));
    // settings only seed the shortcut draft when opening
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, refresh]);

  // Step 1 re-checks until Ollama answers, then moves on by itself.
  const waiting = open && step === 0 && !status?.ollama.reachable;
  useEffect(() => {
    if (!waiting) return;
    const id = window.setInterval(async () => {
      const s = await refresh();
      if (s?.ollama.reachable && stepRef.current === 0) setStep(1);
    }, POLL_MS);
    return () => window.clearInterval(id);
  }, [waiting, refresh]);

  useEffect(() => {
    const { on, off } = eventScope();
    on("pull_progress", (data: PullProgress) => setPull(data));
    on("pull_done", (data: { model: string; ok: boolean; error: string }) => {
      setPull(null);
      if (data.ok) {
        setPullError(null);
        setSelected(data.model);
        void refresh();
      } else {
        setPullError({ model: data.model, error: data.error });
      }
    });
    on("shortcut_status", (data) => setShortcutStatus(data));
    return off;
  }, [refresh]);

  if (!open) return null;

  const copy = (text: string) => {
    void window.nova.copyText(text);
    setCopied(text);
    window.setTimeout(() => setCopied(""), 1500);
  };

  const startPull = (model: string) => {
    setPullError(null);
    setPull({ model, status: "starting", completed: 0, total: 0 });
    void window.nova.call("pullModel", { name: model });
  };

  const useModel = (model: string) => {
    void window.nova.call("selectModel", { name: model });
    setSelected(model);
  };

  const leaveExtras = () => {
    if (settings && shortcut !== (settings.toggle_shortcut ?? "")) {
      onSaveSettings({ ...settings, toggle_shortcut: shortcut });
    }
    setStep(3);
  };

  const finish = () => {
    if (pull) void window.nova.call("cancelPull");
    void window.nova.call("markOnboardingDone");
    onClose();
  };

  const command = (text: string) => (
    <div className="mt-2 flex items-center gap-2 rounded-lg border border-border bg-bg px-3 py-2">
      <code className="min-w-0 flex-1 truncate font-mono text-[12.5px] text-text">{text}</code>
      <button
        className="flex h-7 w-7 shrink-0 cursor-pointer items-center justify-center rounded-md text-meta hover:bg-surface-alt hover:text-text"
        onClick={() => copy(text)}
        aria-label={`Copy ${text}`}
      >
        {copied === text ? <Check size={14} className="text-ok" /> : <Copy size={14} />}
      </button>
    </div>
  );

  const check = (ok: boolean, label: string) => (
    <div className="flex items-center gap-2 text-[13.5px]">
      <span
        className={`flex h-5 w-5 items-center justify-center rounded-full ${ok ? "bg-ok/15 text-ok" : "bg-danger/15 text-danger"}`}
        aria-hidden="true"
      >
        {ok ? <Check size={12} /> : "!"}
      </span>
      <span className={ok ? "text-text" : "text-subtext"}>{label}</span>
    </div>
  );

  const ollamaStep = () => {
    const o = status?.ollama;
    return (
      <div className="flex flex-col gap-3">
        <p className="text-[13.5px] text-subtext">
          Nova runs its models with Ollama, on this computer. Nothing leaves your machine.
        </p>
        {check(!!o?.installed, o?.installed ? "Ollama is installed" : "Ollama is not installed")}
        {check(!!o?.reachable, o?.reachable ? "Ollama is running" : "Ollama is not running")}
        {!o?.reachable && (
          <div>
            <p className="text-[13px] text-subtext">
              {o?.installed ? "Start it in a terminal:" : "Install it in a terminal (it starts by itself):"}
            </p>
            {command(o?.installed ? START_CMD : INSTALL_CMD)}
            <p className="mt-2 flex items-center gap-1.5 text-[12.5px] text-meta" aria-live="polite">
              <RefreshCw size={12} className="animate-spin motion-reduce:animate-none" aria-hidden="true" />
              Checking again every 2 seconds…
            </p>
          </div>
        )}
      </div>
    );
  };

  const modelStep = () => {
    const rec = status?.recommended;
    const pct = pull && pull.total > 0 ? Math.min(100, (100 * pull.completed) / pull.total) : 0;
    const others = (status?.models ?? []).filter((m) => m !== rec?.model);
    const recInstalled = !!rec && (status?.models ?? []).includes(rec.model);
    return (
      <div className="flex flex-col gap-3">
        <p className="flex items-center gap-2 text-[13.5px] text-subtext">
          <Cpu size={15} className="shrink-0 text-meta" aria-hidden="true" />
          {status?.gpu
            ? `${status.gpu.name} · ${Math.round(status.gpu.vram_total_mib / 1024)} GB of video memory`
            : "No NVIDIA GPU found: models run on the CPU, slower."}
        </p>
        {rec && (
          <div className="rounded-xl border border-accent/40 bg-accent/5 p-3.5">
            <div className="flex items-center justify-between gap-3">
              <div className="min-w-0">
                <div className="text-[11.5px] font-semibold uppercase tracking-wide text-accent">Recommended</div>
                <div className="font-mono text-[15px] text-text">{rec.model}</div>
              </div>
              {selected === rec.model ? (
                <span className="flex items-center gap-1 text-[13px] text-ok">
                  <Check size={14} aria-hidden="true" /> Ready
                </span>
              ) : recInstalled ? (
                <Btn variant="accent" onClick={() => useModel(rec.model)}>Use it</Btn>
              ) : !pull ? (
                <Btn variant="accent" onClick={() => startPull(rec.model)}>
                  <span className="flex items-center gap-1.5"><Download size={14} aria-hidden="true" />Download</span>
                </Btn>
              ) : null}
            </div>
            <p className="mt-1.5 text-[12.5px] text-subtext">{rec.note}</p>
          </div>
        )}
        {pull && (
          <div aria-live="polite">
            <div className="mb-1 flex justify-between text-[12.5px] text-subtext">
              <span className="truncate">{pull.model} · {pull.status}</span>
              <span className="tabular shrink-0">
                {pull.total > 0 ? `${gb(pull.completed)} / ${gb(pull.total)} · ${pct.toFixed(0)}%` : ""}
              </span>
            </div>
            <div
              className="h-2 overflow-hidden rounded-full bg-bg"
              role="progressbar"
              aria-label={`Downloading ${pull.model}`}
              aria-valuenow={Math.round(pct)}
              aria-valuemin={0}
              aria-valuemax={100}
            >
              <div className="h-full rounded-full bg-accent transition-[width]" style={{ width: `${pct}%` }} />
            </div>
            <div className="mt-2 flex justify-end">
              <Btn variant="plain" onClick={() => void window.nova.call("cancelPull")}>Cancel</Btn>
            </div>
          </div>
        )}
        {pullError && (
          <div role="alert" className="flex items-center justify-between gap-3 rounded-lg bg-danger/10 px-3 py-2">
            <span className="text-[13px] text-danger">
              {pullError.error === "Cancelled" ? "Download cancelled." : pullError.error}
            </span>
            <Btn onClick={() => startPull(pullError.model)}>Retry</Btn>
          </div>
        )}
        {others.length > 0 && (
          <div>
            <div className="mb-1.5 text-[12.5px] text-meta">Or use a model you already have:</div>
            <div className="flex flex-wrap gap-1.5">
              {others.map((m) => (
                <button
                  key={m}
                  className={`cursor-pointer rounded-lg border px-2.5 py-1 font-mono text-[12.5px] transition-colors ${
                    selected === m
                      ? "border-ok/60 text-ok"
                      : "border-border text-subtext hover:border-accent/60 hover:text-text"
                  }`}
                  onClick={() => useModel(m)}
                  aria-pressed={selected === m}
                >
                  {m}
                </button>
              ))}
            </div>
          </div>
        )}
      </div>
    );
  };

  const extrasStep = () => (
    <div className="flex flex-col gap-4">
      <div>
        <div className="mb-1 flex items-center gap-2 text-sm text-text">
          <Globe size={15} className="text-meta" aria-hidden="true" /> Web search
          <span className="text-[12px] text-meta">optional</span>
        </div>
        {status?.searxng ? (
          check(true, "SearXNG found at localhost:8888")
        ) : (
          <p className="text-[13px] text-subtext">
            Not set up. Nova searches the web through your own SearXNG at localhost:8888, with the JSON
            format enabled. The README explains how; you can do it any time.
          </p>
        )}
      </div>
      {/* ShortcutField brings its own "Show/hide shortcut" label */}
      <div>
        <ShortcutField
          value={shortcut}
          savedValue={settings?.toggle_shortcut ?? ""}
          status={shortcutStatus}
          onChange={setShortcut}
        />
      </div>
    </div>
  );

  const readyStep = () => (
    <div className="flex flex-col gap-3">
      <p className="text-[13.5px] text-subtext">
        Nova acts on this computer only through tools, and you stay in control:
      </p>
      {[
        ["Read", "text-ok", "Looks things up (system info, files, git) without asking."],
        ["Write", "text-warn", "Changes something: asks you first."],
        ["Destructive", "text-danger", "Deletes or can't be undone: always asks, and shows exactly what will run."],
      ].map(([name, tone, text]) => (
        <div key={name} className="flex gap-3 text-[13.5px]">
          <span className={`w-24 shrink-0 font-semibold ${tone}`}>{name}</span>
          <span className="text-subtext">{text}</span>
        </div>
      ))}
      <p className="flex items-center gap-2 text-[12.5px] text-meta">
        <ShieldCheck size={14} aria-hidden="true" /> Run this setup again any time from Settings → General.
      </p>
    </div>
  );

  const next =
    step === 0 ? (
      <Btn variant="accent" disabled={!status?.ollama.reachable} onClick={() => setStep(1)}>Next</Btn>
    ) : step === 1 ? (
      <Btn variant="accent" disabled={!!pull} onClick={() => setStep(2)}>Next</Btn>
    ) : step === 2 ? (
      <Btn variant="accent" onClick={leaveExtras}>Next</Btn>
    ) : (
      <Btn variant="accent" onClick={finish} initialFocus>Start chatting</Btn>
    );

  return (
    <Dialog
      title="Set up Nova"
      onClose={finish}
      width={560}
      footer={
        <>
          {step > 0 && <Btn variant="plain" onClick={() => setStep(step - 1)}>Back</Btn>}
          {step < 3 && (
            <Btn variant="plain" onClick={() => (step === 2 ? leaveExtras() : setStep(step + 1))}>Skip</Btn>
          )}
          {next}
        </>
      }
    >
      <ol className="mb-4 flex items-center gap-2" aria-label="Setup steps">
        {STEPS.map((name, i) => (
          <li key={name} className="flex flex-1 items-center gap-2" aria-current={i === step ? "step" : undefined}>
            <span
              className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-[12px] font-semibold ${
                i < step ? "bg-ok/20 text-ok" : i === step ? "bg-accent text-accent-text" : "bg-surface-alt text-meta"
              }`}
            >
              {i < step ? <Check size={12} aria-hidden="true" /> : i + 1}
            </span>
            <span className={`text-[12.5px] ${i === step ? "text-text" : "text-meta"}`}>{name}</span>
            {i < STEPS.length - 1 && <span className="h-px flex-1 bg-border" aria-hidden="true" />}
          </li>
        ))}
      </ol>
      {step === 0 && ollamaStep()}
      {step === 1 && modelStep()}
      {step === 2 && extrasStep()}
      {step === 3 && readyStep()}
    </Dialog>
  );
}
