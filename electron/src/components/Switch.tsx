export function Switch({
  checked,
  onChange,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <button
      className="switch"
      data-on={checked}
      role="switch"
      aria-checked={checked}
      onClick={() => onChange(!checked)}
    />
  );
}