import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";
import type { NovaApi } from "../lib/types";

/** A fresh window.nova stub; `emit` delivers a sidecar event to subscribers. */
export function stubNova() {
  const handlers = new Map<string, Set<(params: unknown[]) => void>>();
  const nova = {
    call: vi.fn(),
    copyText: vi.fn(async () => true),
    openArtifact: vi.fn(async () => true),
    onEvent: vi.fn((event: string, cb: (params: unknown[]) => void) => {
      if (!handlers.has(event)) handlers.set(event, new Set());
      handlers.get(event)!.add(cb);
      return () => handlers.get(event)!.delete(cb);
    }),
  };
  window.nova = nova as unknown as NovaApi;
  const emit = (event: string, data: unknown) =>
    handlers.get(event)?.forEach((cb) => cb([data]));
  return { nova, emit };
}

afterEach(() => cleanup());
