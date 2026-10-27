import { useEffect, useRef } from "react";
import { FitAddon } from "@xterm/addon-fit";
import { Terminal } from "@xterm/xterm";
import { ChevronDown } from "lucide-react";
import "@xterm/xterm/css/xterm.css";

/**
 * Bottom dock wrapping the backend's shared shell session (AD-022).
 *
 * The terminal is a VIEW on the same session `shell.execute` uses: raw
 * keystrokes go to `terminal_input`, decoded `term_output` chunks are drawn
 * by xterm, and the PTY is resized to match the panel. Ctrl+C sent here
 * interrupts whatever command the LLM is currently running on the session.
 */
export function TerminalPanel({ onClose }: { onClose: () => void }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const termRef = useRef<Terminal | null>(null);
  const fitRef = useRef<FitAddon | null>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const term = new Terminal({
      convertEol: false,
      fontFamily: '"JetBrains Mono", "Noto Sans Mono", monospace',
      fontSize: 13,
      lineHeight: 1.15,
      cursorBlink: true,
      // Night palette (styles.css @theme); ANSI colours mapped to its tones.
      theme: {
        background: "#0f0e15",
        foreground: "#eeeaf6",
        cursor: "#f7b267",
        cursorAccent: "#1a1309",
        selectionBackground: "#3a3448",
        black: "#26232f",
        red: "#ff8a85",
        green: "#7fdbb0",
        yellow: "#e8d06b",
        blue: "#9fb4ff",
        magenta: "#c3b5ff",
        cyan: "#86d4e0",
        white: "#d8d3e4",
        brightBlack: "#9a93ae",
        brightRed: "#ffaaa6",
        brightGreen: "#a5ebc9",
        brightYellow: "#f2e08f",
        brightBlue: "#bccbff",
        brightMagenta: "#d9cfff",
        brightCyan: "#a9e4ed",
        brightWhite: "#ffffff",
      },
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    termRef.current = term;
    fitRef.current = fit;

    // Subscribe BEFORE terminal_open: the replay chunks arrive in the same
    // round-trip, and xterm buffers writes until the terminal is opened.
    const unsub =
      window.nova.onEvent?.("term_output", (params) => {
        const chunk = params[0];
        if (typeof chunk === "string") term.write(atob(chunk));
      }) ?? (() => {});
    window.nova.call("terminal_open");

    term.onData((data) => {
      window.nova.call("terminal_input", { data });
    });

    term.open(container);

    const sendResize = () => {
      window.nova.call("terminal_resize", { cols: term.cols, rows: term.rows });
    };
    const refit = () => {
      if (!termRef.current || !fitRef.current) return;
      fitRef.current.fit();
      sendResize();
    };
    // Fit after the container gets its real size (once the dock is shown).
    requestAnimationFrame(refit);
    const ro = new ResizeObserver(() => requestAnimationFrame(refit));
    ro.observe(container);

    return () => {
      ro.disconnect();
      unsub();
      window.nova.call("terminal_close");
      term.dispose();
      termRef.current = null;
      fitRef.current = null;
    };
  }, []);

  return (
    <div className="shrink-0 border-t border-border bg-surface">
      <div className="flex h-9 items-center gap-2.5 px-4">
        <span className="text-[13px] font-semibold text-text">Terminal</span>
        <span className="truncate text-[12px] text-meta">
          Same shell Nova uses. Ctrl+C stops the running command.
        </span>
        <button
          className="ml-auto -mr-1.5 inline-flex h-7 w-7 cursor-pointer items-center justify-center rounded-lg text-meta hover:bg-surface-alt hover:text-text"
          onClick={onClose}
          title="Hide terminal"
          aria-label="Hide terminal"
        >
          <ChevronDown size={12} />
        </button>
      </div>
      <div ref={containerRef} className="h-[220px] bg-code px-3 py-2">
        {/* xterm is attached here */}
      </div>
    </div>
  );
}