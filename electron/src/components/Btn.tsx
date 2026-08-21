import React from "react";

const STYLES: Record<string, string> = {
  ghost:
    "border border-border bg-transparent text-subtext hover:border-accent hover:text-accent rounded-none px-3 h-7 font-mono text-[11px]",
  accent:
    "bg-accent text-accent-text hover:shadow-[0_0_14px_rgba(138,230,140,0.45)] rounded-none px-3 h-7 font-mono text-[11px] font-bold",
  danger:
    "text-danger hover:bg-danger/10 rounded-none px-2 h-7 font-mono text-[11px] font-bold",
  plain:
    "text-meta hover:bg-surface-alt hover:text-accent-2 rounded-none px-2 h-7 font-mono text-[11px]",
};

export function Btn({
  children,
  onClick,
  variant = "ghost",
  disabled,
  className = "",
  title,
}: {
  children: React.ReactNode;
  onClick?: () => void;
  variant?: "ghost" | "accent" | "danger" | "plain";
  disabled?: boolean;
  className?: string;
  title?: string;
}) {
  return (
    <button
      className={`inline-flex cursor-pointer items-center justify-center gap-1.5 disabled:cursor-default disabled:opacity-40 ${STYLES[variant]} ${className}`}
      onClick={onClick}
      disabled={disabled}
      title={title}
    >
      {children}
    </button>
  );
}