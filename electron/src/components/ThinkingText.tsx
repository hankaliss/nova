import { useEffect, useState } from "react";

const PHRASES = [
  "thinking",
  "reasoning",
  "pondering",
  "contemplating",
  "computing",
  "distilling",
  "smooshing tokens",
  "herding electrons",
  "warming capacitors",
  "aligning probabilities",
  "consulting local knowledge",
  "brewing",
];

export function ThinkingText({
  phrases = PHRASES,
  intervalMs = 1400,
}: {
  phrases?: string[];
  intervalMs?: number;
}) {
  const [idx, setIdx] = useState(0);

  useEffect(() => {
    const id = window.setInterval(
      () => setIdx((i) => (i + 1) % phrases.length),
      intervalMs
    );
    return () => window.clearInterval(id);
  }, [phrases, intervalMs]);

  return (
    <span
      className="inline-flex items-center gap-2 font-mono text-[11.5px] text-subtext"
      data-thinking=""
    >
      <span key={idx} className="msg-in">
        {phrases[idx]}
        <span className="animate-pulse text-faint">…</span>
      </span>
      <span className="caret shrink-0" />
    </span>
  );
}
