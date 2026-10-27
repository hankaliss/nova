// Nova's mark: an eight-point star (a nova is a star that suddenly flares).
// Four long rays, four short ones. `flare` makes it pulse while Nova works.

function starPoints(): string {
  const points: string[] = [];
  for (let i = 0; i < 16; i++) {
    const angle = (Math.PI / 8) * i - Math.PI / 2;
    // even indices are ray tips (long on the axes, short on diagonals),
    // odd indices are the valleys between rays
    const r = i % 2 === 1 ? 2.1 : i % 4 === 0 ? 11.5 : 5.6;
    points.push(
      `${(12 + r * Math.cos(angle)).toFixed(2)},${(12 + r * Math.sin(angle)).toFixed(2)}`
    );
  }
  return points.join(" ");
}

const POINTS = starPoints();

export function NovaMark({
  size = 16,
  flare = false,
  className = "",
}: {
  size?: number;
  flare?: boolean;
  className?: string;
}) {
  return (
    <svg
      className={`nova-mark shrink-0 ${className}`}
      data-flare={flare}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      aria-hidden="true"
    >
      <polygon points={POINTS} fill="currentColor" />
    </svg>
  );
}
