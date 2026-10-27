import { useRef } from "react";

/** Composer input history: ArrowUp/ArrowDown recall sent messages, like a shell. */
export function useComposerHistory(input: string, setInput: (value: string) => void) {
  const inputHistoryRef = useRef<string[]>([]);
  const historyIndexRef = useRef<number | null>(null);

  const recordSent = (t: string) => {
    const hist = inputHistoryRef.current;
    if (hist[hist.length - 1] !== t) hist.push(t);
    if (hist.length > 100) hist.shift();
  };

  const composerKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    const hist = inputHistoryRef.current;
    if (e.key === "ArrowUp") {
      if (hist.length === 0) return;
      // Start recalling only from an empty input; ArrowUp stays a caret
      // key during normal (multiline) editing. Once navigating, Up walks
      // further back through the sent messages.
      if (historyIndexRef.current === null) {
        if (input !== "") return;
        e.preventDefault();
        historyIndexRef.current = hist.length - 1;
        setInput(hist[hist.length - 1]);
      } else {
        e.preventDefault();
        const idx = Math.max(0, historyIndexRef.current - 1);
        historyIndexRef.current = idx;
        setInput(hist[idx]);
      }
    } else if (e.key === "ArrowDown" && historyIndexRef.current !== null) {
      e.preventDefault();
      const next = historyIndexRef.current + 1;
      if (next >= hist.length) {
        historyIndexRef.current = null;
        setInput("");
      } else {
        historyIndexRef.current = next;
        setInput(hist[next]);
      }
    }
  };

  const onComposerChange = (value: string) => {
    // Manual edit ends recall mode; arrows return to caret navigation.
    historyIndexRef.current = null;
    setInput(value);
  };

  /** A send ends recall mode. */
  const endRecall = () => {
    historyIndexRef.current = null;
  };

  return { recordSent, endRecall, composerKeyDown, onComposerChange };
}
