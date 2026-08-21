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
}

export type Settings = Record<string, string>;

export interface NovaApi {
  call(method: string, params?: unknown): void;
  copyText(text: string): Promise<boolean>;
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