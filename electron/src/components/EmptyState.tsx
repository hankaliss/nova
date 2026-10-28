import { Gauge, HardDrive, MemoryStick, Sparkles } from "lucide-react";
import { NovaMark } from "./NovaMark";

const STARTERS = [
  { icon: Gauge, text: "Generate a system report" },
  { icon: MemoryStick, text: "How much GPU memory is in use?" },
  { icon: HardDrive, text: "How much disk space is left?" },
  { icon: Sparkles, text: "What can you do on this computer?" },
];

export function EmptyState({
  ollamaOk,
  modelCount,
  model,
  onStarter,
  onOpenSetup,
}: {
  ollamaOk: boolean;
  modelCount: number;
  model: string;
  onStarter: (text: string) => void;
  onOpenSetup?: () => void;
}) {
  const status = !ollamaOk
    ? "Ollama isn't reachable. Start it (systemctl start ollama), then reopen Nova."
    : model
      ? `${model} is loaded and runs entirely on this computer.`
      : modelCount > 0
        ? `Pick one of your ${modelCount} local models to begin.`
        : "No models yet. Open setup, or pull one with: ollama pull qwen3.5:4b";

  return (
    <div className="flex flex-1 flex-col items-center justify-center py-10">
      <NovaMark size={64} className="mb-6" />
      <h1 className="font-display max-w-[18ch] text-center text-[34px] font-semibold leading-[1.1] tracking-[-0.02em] text-text [text-wrap:balance]">
        What should we look at on this machine?
      </h1>
      <p
        className={`mt-3 max-w-[52ch] text-center text-[14.5px] ${
          ollamaOk ? "text-subtext" : "text-danger"
        }`}
      >
        {status}
      </p>
      {onOpenSetup && (!ollamaOk || modelCount === 0) && (
        <button
          className="mt-4 cursor-pointer rounded-lg bg-accent px-4 py-2 text-[13.5px] font-semibold text-accent-text hover:bg-accent-2"
          onClick={onOpenSetup}
        >
          Open setup
        </button>
      )}

      <div className="mt-8 grid w-full max-w-[560px] grid-cols-2 gap-2.5">
        {STARTERS.map(({ icon: Icon, text }) => (
          <button
            key={text}
            className="group flex cursor-pointer items-start gap-3 rounded-xl border border-border bg-surface px-3.5 py-3 text-left text-[13.5px] text-subtext transition-colors hover:border-accent/60 hover:text-text disabled:cursor-default disabled:opacity-50"
            onClick={() => onStarter(text)}
            disabled={!ollamaOk}
          >
            <Icon
              size={17}
              className="mt-px shrink-0 text-meta transition-colors group-hover:text-accent"
              aria-hidden="true"
            />
            <span>{text}</span>
          </button>
        ))}
      </div>
    </div>
  );
}
