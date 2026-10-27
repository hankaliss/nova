import React from "react";

const STYLES: Record<string, string> = {
  ghost:
    "h-8 rounded-lg border border-border px-3 text-[13px] text-subtext hover:border-accent/60 hover:bg-surface-alt hover:text-text",
  accent:
    "h-8 rounded-lg bg-accent px-3.5 text-[13px] font-semibold text-accent-text hover:bg-accent-2",
  danger:
    "h-8 rounded-lg px-3 text-[13px] font-semibold text-danger hover:bg-danger/10",
  plain:
    "h-8 rounded-lg px-2.5 text-[13px] text-subtext hover:bg-surface-alt hover:text-text",
  icon:
    "h-8 w-8 rounded-lg text-meta hover:bg-surface-alt hover:text-text",
};

export function Btn({
  children,
  onClick,
  variant = "ghost",
  disabled,
  className = "",
  title,
  initialFocus,
  ariaLabel,
  pressed,
}: {
  children: React.ReactNode;
  onClick?: () => void;
  variant?: "ghost" | "accent" | "danger" | "plain" | "icon";
  disabled?: boolean;
  className?: string;
  title?: string;
  /** Receive focus when the enclosing Dialog opens. */
  initialFocus?: boolean;
  /** Required for icon-only buttons. */
  ariaLabel?: string;
  /** Toggle buttons: rendered with aria-pressed and an active look. */
  pressed?: boolean;
}) {
  return (
    <button
      className={`inline-flex shrink-0 cursor-pointer items-center justify-center gap-1.5 transition-colors disabled:cursor-default disabled:opacity-40 ${STYLES[variant]} ${pressed ? "bg-surface-alt text-accent" : ""} ${className}`}
      onClick={onClick}
      disabled={disabled}
      title={title}
      data-autofocus={initialFocus || undefined}
      aria-label={ariaLabel}
      aria-pressed={pressed}
    >
      {children}
    </button>
  );
}