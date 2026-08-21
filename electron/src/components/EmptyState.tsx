export function EmptyState({
  ollamaOk,
  modelCount,
  onStarter,
}: {
  ollamaOk: boolean;
  modelCount: number;
  onStarter: (text: string) => void;
}) {
  return (
    <div className="flex h-full items-center justify-center overflow-hidden">
      <div className="flex w-[460px] flex-col items-center gap-4">
        {/* Wordmark */}
        <div className="glow-text font-mono text-[26px] font-bold lowercase tracking-[0.32em]">
          nova<span className="animate-pulse">▊</span>
        </div>

        <p className="text-center font-mono text-[11.5px] text-subtext">
          local-first assistant · runs on your hardware · stays on your machine
        </p>

        {/* Connection status */}
        <div className="flex items-center gap-1.5 font-mono text-[10.5px]">
          <span
            className={`h-[6px] w-[6px] rounded-full ${
              ollamaOk
                ? "bg-ok shadow-[0_0_6px_rgba(110,231,160,0.8)]"
                : "bg-danger"
            }`}
          />
          <span className={ollamaOk ? "text-meta" : "text-danger"}>
            {ollamaOk
              ? `ollama connected${
                  modelCount > 0
                    ? ` · ${modelCount} model${modelCount === 1 ? "" : "s"}`
                    : ""
                }`
              : "ollama not reachable — start the service, then restart nova"}
          </span>
        </div>

        {/* Starter commands */}
        <div className="mt-1 flex w-full flex-col gap-1.5">
          {[
            "generate a system report",
            "what can you do?",
            "how much gpu memory is being used?",
            "list my running services",
          ].map((s) => (
            <button
              key={s}
              className="group flex w-full cursor-pointer items-baseline gap-2 rounded-none border border-border bg-surface px-3 py-2 text-left font-mono text-[11.5px] transition-colors hover:border-accent hover:bg-surface-alt"
              onClick={() => onStarter(s)}
            >
              <span className="shrink-0 select-none text-accent">$</span>
              <span className="text-subtext group-hover:text-accent-2">{s}</span>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
