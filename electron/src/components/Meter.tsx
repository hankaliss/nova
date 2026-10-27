/** Thin usage meter; turns warn/danger as it fills. */
export function Meter({
  used,
  total,
  label,
}: {
  used: number;
  total: number;
  label: string;
}) {
  const pct = total > 0 ? Math.min(1, used / total) : 0;
  const color = pct > 0.92 ? "bg-danger" : pct > 0.8 ? "bg-warn" : "bg-accent";
  return (
    <div
      role="meter"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={total}
      aria-valuenow={used}
      className="h-1.5 flex-1 overflow-hidden rounded-full bg-surface-alt"
    >
      <div
        className={`h-full rounded-full transition-[width] duration-500 ${color}`}
        style={{ width: `${pct * 100}%` }}
      />
    </div>
  );
}
