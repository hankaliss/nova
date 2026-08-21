import { useEffect, useState } from "react";

/**
 * Select-to-copy: selecting text inside `ref` copies it to the clipboard
 * and shows a brief toast. Inputs/textareas are excluded so editing
 * gestures never trigger a copy.
 */
export function useSelectToCopy(ref: React.RefObject<HTMLElement | null>) {
  const [toast, setToast] = useState<string | null>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    let timer: number | undefined;

    const onMouseUp = () => {
      window.setTimeout(() => {
        try {
          const sel = window.getSelection();
          const text = sel?.toString().trim() ?? "";
          if (!sel || sel.isCollapsed || text.length === 0) return;
          const anchor = sel.anchorNode;
          const host = anchor?.nodeType === 1 ? (anchor as Element) : anchor?.parentElement;
          if (anchor && !el.contains(host as Element)) return;
          if (host?.closest("input, textarea")) return;
          void window.nova.copyText(text);
          setToast(`copied ${text.length} char${text.length === 1 ? "" : "s"}`);
          window.clearTimeout(timer);
          timer = window.setTimeout(() => setToast(null), 1600);
        } catch {
          /* clipboard failures must never break interaction */
        }
      }, 0);
    };

    el.addEventListener("mouseup", onMouseUp);
    return () => {
      el.removeEventListener("mouseup", onMouseUp);
      window.clearTimeout(timer);
    };
  }, [ref]);

  return toast;
}
