export function CopyToast({ text }: { text: string | null }) {
  if (!text) return null;
  return (
    <div className="pointer-events-none fixed bottom-16 left-1/2 z-50 -translate-x-1/2 rounded-sm border border-accent/40 bg-surface px-3 py-1.5 font-mono text-[10.5px] text-accent shadow-[0_0_18px_rgba(0,0,0,0.55)]">
      <span className="glow-text">[✓]</span> {text}
    </div>
  );
}
