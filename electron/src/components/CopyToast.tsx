import { Check } from "lucide-react";

export function CopyToast({ text }: { text: string | null }) {
  return (
    <div aria-live="polite" className="pointer-events-none fixed bottom-24 left-1/2 z-50 -translate-x-1/2">
      {text && (
        <div data-copy-toast className="msg-in flex items-center gap-2 rounded-full border border-border bg-surface-alt px-4 py-2 text-[13px] text-text shadow-[0_8px_30px_rgba(0,0,0,0.45)]">
          <Check size={14} className="text-ok" aria-hidden="true" />
          {text}
        </div>
      )}
    </div>
  );
}
