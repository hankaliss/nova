import { fmtMiB, fmtGB } from "../lib/format";

function Bar({
  used,
  total,
  color = "bg-accent",
}: {
  used: number;
  total: number;
  color?: string;
}) {
  const pct = total > 0 ? Math.min(1, used / total) : 0;
  return (
    <div className="h-[4px] flex-1 overflow-hidden rounded-full bg-surface-alt">
      <div
        className={`h-full rounded-full ${color}`}
        style={{ width: `${pct * 100}%` }}
      />
    </div>
  );
}

export function Inspector({
  modelInfo,
  response,
  system,
}: {
  modelInfo: Record<string, string | number>;
  response: Record<string, string | number>;
  system: Record<string, any>;
}) {
  const gpu = system.gpu || {};
  const mem = system.memory || {};

  return (
    <div className="flex flex-col gap-3 overflow-y-auto text-[12px]">
      {/* Model card */}
      <div className="rounded-lg bg-bg p-3">
        <div className="font-mono text-[9px] font-bold tracking-[0.18em] text-meta">
          MODEL
        </div>
        <div className="mt-1 truncate font-mono text-[12px] font-bold text-accent">
          {modelInfo.name || "—"}
        </div>
        <div className="font-mono text-[10px] text-subtext">
          {[modelInfo.parameter_size, modelInfo.quantization]
            .filter(Boolean)
            .join(" · ") || "—"}
          {modelInfo.context_length
            ? ` · ${modelInfo.context_length} ctx`
            : ""}
        </div>
      </div>

      {/* Response card */}
      <div className="rounded-lg bg-bg p-3">
        <div className="font-mono text-[9px] font-bold tracking-[0.18em] text-meta">
          LAST RESPONSE
        </div>
        <div className="mt-1 font-mono text-[11px] text-text">
          {response.tokens
            ? `${response.tokens} tokens · ${
                response.eval_ns
                  ? (
                      Number(response.tokens) /
                      (Number(response.eval_ns) / 1e9)
                    ).toFixed(1)
                  : "—"
              } tok/s`
            : "No response yet"}
        </div>
        {response.prompt_tokens ? (
          <div className="font-mono text-[10px] text-subtext">
            Prompt: {response.prompt_tokens} tokens
          </div>
        ) : null}
        {response.tools ? (
          <div className="font-mono text-[10px] text-warn">
            Tools used: {response.tools}
          </div>
        ) : null}
        {response.model ? (
          <div className="truncate font-mono text-[10px] text-subtext">
            Model: {response.model}
          </div>
        ) : null}
      </div>

      {/* System card */}
      <div className="rounded-lg bg-bg p-3">
        <div className="font-mono text-[9px] font-bold tracking-[0.18em] text-meta">
          SYSTEM
        </div>
        <div className="mt-1 truncate font-mono text-[11px] text-text">
          {gpu.name || "GPU —"}
        </div>

        {/* VRAM */}
        <div className="mt-2 flex items-center gap-2">
          <span className="font-mono text-[10px] text-subtext">
            {gpu.vram_used_mib !== undefined
              ? `${fmtMiB(gpu.vram_used_mib)} / ${fmtMiB(gpu.vram_total_mib)}`
              : "VRAM —"}
          </span>
          <Bar used={gpu.vram_used_mib || 0} total={gpu.vram_total_mib || 0} />
        </div>

        {/* GPU temp/util */}
        {gpu.temperature_c ? (
          <div
            className={`mt-1 font-mono text-[10px] ${
              gpu.temperature_c > 80 ? "text-danger" : "text-subtext"
            }`}
          >
            {gpu.temperature_c} °C
            {gpu.utilization_pct !== undefined
              ? ` · ${gpu.utilization_pct}% util`
              : ""}
          </div>
        ) : null}

        {/* RAM */}
        <div className="mt-2 flex items-center gap-2">
          <span className="font-mono text-[10px] text-subtext">
            {mem.used_bytes !== undefined
              ? `${fmtGB(mem.used_bytes)} / ${fmtGB(mem.total_bytes)}`
              : "RAM —"}
          </span>
          <Bar
            used={mem.used_bytes || 0}
            total={mem.total_bytes || 0}
            color="bg-ok"
          />
        </div>

        <div className="mt-1 font-mono text-[10px] text-subtext">
          {system.cpu?.cores ? `${system.cpu.cores} cores` : "CPU —"}
        </div>
      </div>
    </div>
  );
}