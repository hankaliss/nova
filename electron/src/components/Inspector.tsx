import type { ReactNode } from "react";
import { fmtGB, fmtGiB } from "../lib/format";
import { ContextSize } from "../lib/types";
import { Meter } from "./Meter";

/** One labelled reading: name on the left, value on the right. */
function Row({ label, value, tone = "text-text" }: { label: string; value: string; tone?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-1 text-[13px]">
      <span className="text-meta">{label}</span>
      <span className={`tabular truncate text-right ${tone}`}>{value}</span>
    </div>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="border-b border-border pb-4 last:border-b-0">
      <h2 className="font-display mb-2 text-[15px] font-semibold text-text">{title}</h2>
      {children}
    </section>
  );
}

export function Inspector({
  modelInfo,
  response,
  system,
  context,
}: {
  modelInfo: Record<string, string | number>;
  response: Record<string, string | number>;
  system: Record<string, any>;
  context: ContextSize | null;
}) {
  const gpu = system.gpu || {};
  const mem = system.memory || {};
  const tps =
    response.tokens && response.eval_ns
      ? (Number(response.tokens) / (Number(response.eval_ns) / 1e9)).toFixed(1)
      : null;

  return (
    <div className="flex flex-col gap-4">
      <Section title="Model">
        <p className="truncate pb-1 text-[14px] font-semibold text-accent">
          {modelInfo.name || "No model selected"}
        </p>
        {modelInfo.parameter_size ? (
          <Row label="Size" value={String(modelInfo.parameter_size)} />
        ) : null}
        {modelInfo.quantization ? (
          <Row label="Quantization" value={String(modelInfo.quantization)} />
        ) : null}
        {context && context.model === modelInfo.name ? (
          <Row
            label={context.auto ? "Context in use (auto)" : "Context in use"}
            value={
              context.fitting
                ? "Measuring…"
                : `${Number(context.num_ctx).toLocaleString()} tokens`
            }
            tone={context.on_gpu === false ? "text-warn" : "text-text"}
          />
        ) : null}
        {context?.on_gpu === false && !context.fitting ? (
          <p className="pb-1 text-[12.5px] text-warn">
            Doesn't fit fully on the GPU at this size; answers will be slow.
          </p>
        ) : null}
        {modelInfo.context_length ? (
          <Row
            label="Trained for"
            value={`${Number(modelInfo.context_length).toLocaleString()} tokens`}
          />
        ) : null}
      </Section>

      <Section title="Last answer">
        {response.tokens ? (
          <>
            <Row label="Tokens written" value={String(response.tokens)} />
            {tps ? <Row label="Speed" value={`${tps} tok/s`} /> : null}
            {response.prompt_tokens ? (
              <Row label="Prompt" value={`${response.prompt_tokens} tokens`} />
            ) : null}
            {response.tools ? (
              <Row label="Tools used" value={String(response.tools)} tone="text-warn" />
            ) : null}
          </>
        ) : (
          <p className="text-[13px] text-meta">Nothing yet. Ask something to see speed and token counts.</p>
        )}
      </Section>

      <Section title="This computer">
        <p className="truncate pb-2 text-[13.5px] text-subtext">{gpu.name || "GPU not detected"}</p>
        <div className="flex flex-col gap-3">
          <div>
            <div className="mb-1.5 flex justify-between text-[12.5px]">
              <span className="text-meta">GPU memory</span>
              <span className="tabular text-text">
                {gpu.vram_used_mib !== undefined
                  ? `${fmtGiB(gpu.vram_used_mib)} of ${fmtGiB(gpu.vram_total_mib)}`
                  : "—"}
              </span>
            </div>
            <Meter
              used={gpu.vram_used_mib || 0}
              total={gpu.vram_total_mib || 0}
              label="GPU memory in use"
            />
          </div>
          <div>
            <div className="mb-1.5 flex justify-between text-[12.5px]">
              <span className="text-meta">System memory</span>
              <span className="tabular text-text">
                {mem.used_bytes !== undefined
                  ? `${fmtGB(mem.used_bytes)} of ${fmtGB(mem.total_bytes)}`
                  : "—"}
              </span>
            </div>
            <Meter
              used={mem.used_bytes || 0}
              total={mem.total_bytes || 0}
              label="System memory in use"
            />
          </div>
          <div>
            {gpu.temperature_c ? (
              <Row
                label="GPU temperature"
                value={`${gpu.temperature_c} °C`}
                tone={gpu.temperature_c > 80 ? "text-danger" : "text-text"}
              />
            ) : null}
            {gpu.utilization_pct !== undefined ? (
              <Row label="GPU load" value={`${gpu.utilization_pct}%`} />
            ) : null}
            {system.cpu?.cores ? (
              <Row label="CPU cores" value={String(system.cpu.cores)} />
            ) : null}
          </div>
        </div>
      </Section>
    </div>
  );
}
