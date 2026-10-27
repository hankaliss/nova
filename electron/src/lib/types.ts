export interface ToolEvent {
  name: string;
  args: string;
  status: "running" | "ok" | "fail";
  durationMs: number;
  error: string;
  outputPath: string | undefined;
}

export interface Message {
  id: number;
  role: "user" | "assistant";
  content: string;
  streaming: boolean;
  tools: ToolEvent[];
  created_at?: number;
}

export type Settings = Record<string, string>;

/** What the model is doing right now (derived from live events in useNova). */
export type ActivityPhase =
  | "idle" // no turn in progress
  | "waiting" // turn started, no tokens yet (model load / prompt eval)
  | "thinking" // generating, but no token for ~1.4s (reasoning gap)
  | "generating" // tokens flowing right now
  | "running" // a tool is executing
  | "approval" // waiting for the user to grant/deny a tool
  | "done"; // turn finished — brief summary before fading out

export interface ActivityStep {
  kind: "start" | "generate" | "tool" | "done" | "stop";
  t: number; // seconds since the turn started
  label: string;
  status?: "running" | "ok" | "fail";
  durationMs?: number;
  detail?: string;
}

/** A multiple-choice question the LLM is waiting on (nova.presentChoices). */
export interface PendingChoices {
  question: string;
  options: string[];
}

export interface ActivityState {
  phase: ActivityPhase;
  /** Active tool name while running or awaiting approval. */
  tool?: string;
  tokens: number; // tokens received this turn
  tps: number; // rolling tokens/second (0 when not streaming)
  elapsed: number; // seconds since the turn started
  /** Why the turn is still waiting (e.g. the context size is being measured). */
  note?: string;
  steps: ActivityStep[];
  /** Final stats shown during the "done" phase. */
  summary?: {
    label: string; // "completed" | "stopped" | error text
    tokens: number;
    tps: number;
    tools: number;
    elapsed: number;
  };
}

export interface ModelDetail {
  name: string;
  size: number;
  parameter_size: string;
  quantization: string;
  context_length: number;
  capabilities: string[];
}

export interface NovaApi {
  /** Resolves with the sidecar method's result. */
  call(method: string, params?: unknown): Promise<any>;
  copyText(text: string): Promise<boolean>;
  openAuthUrl?(): Promise<boolean>;
  openArtifact?(html: string): Promise<boolean>;
  setCompact?(compact: boolean): Promise<boolean>;
  onEvent?(
    event: string,
    callback: (params: unknown[]) => void
  ): () => void;
}

declare global {
  interface Window {
    nova: NovaApi;
  }
}
/** Context size the sidecar uses for the selected model (`context_size` event). */
export interface ContextSize {
  model: string;
  num_ctx: number;
  auto: boolean;
  /** Fully on the GPU at this size; null until measured. */
  on_gpu: boolean | null;
  /** An automatic fit (trial model loads) is running. */
  fitting: boolean;
}

/** A fact the user asked Nova to remember. */
export interface Memory {
  id: number;
  content: string;
  created_at: number;
}
