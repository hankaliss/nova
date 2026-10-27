/** Sidecar event subscriptions (preload's window.nova.onEvent). */

/** Events carry their params as an array; single values are unwrapped. */
function unwrap(params: any) {
  return Array.isArray(params) && params.length === 1 ? params[0] : params;
}

/**
 * Collect `on(event, cb)` subscriptions for one effect; `off` removes them
 * all (return it from the effect).
 */
export function eventScope() {
  const unsubs: (() => void)[] = [];
  const on = (event: string, cb: (data: any) => void) => {
    const unsub = window.nova.onEvent?.(event, (params: any) => cb(unwrap(params)));
    if (unsub) unsubs.push(unsub);
  };
  const off = () => unsubs.forEach((u) => u());
  return { on, off };
}
