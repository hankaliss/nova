export function fmtMiB(n: number | undefined | null): string {
  return n === undefined || n === null ? "—" : `${n} MiB`;
}

export function fmtGB(bytes: number | undefined | null): string {
  if (bytes === undefined || bytes === null) return "—";
  return `${(bytes / 1e9).toFixed(1)} GB`;
}

export function prettyJson(json: string): string {
  try {
    return JSON.stringify(JSON.parse(json), null, 2);
  } catch {
    return json;
  }
}
/** MiB → "3.1 GB" (one decimal), for compact VRAM readouts. */
export function fmtGiB(mib: number | undefined | null): string {
  if (mib === undefined || mib === null) return "—";
  return `${(mib / 1024).toFixed(1)} GB`;
}
