import { describe, expect, it } from "vitest";
import { fromEvent } from "./shortcutKeys";

const key = (code: string, mods: Partial<Record<"metaKey" | "ctrlKey" | "altKey" | "shiftKey", boolean>> = {}) => ({
  code,
  metaKey: false,
  ctrlKey: false,
  altKey: false,
  shiftKey: false,
  ...mods,
});

describe("fromEvent", () => {
  it("builds the sidecar's shortcut text in Qt's modifier order", () => {
    expect(fromEvent(key("Space", { metaKey: true }))).toEqual({ text: "Meta+Space" });
    expect(fromEvent(key("KeyN", { ctrlKey: true, altKey: true }))).toEqual({ text: "Ctrl+Alt+N" });
    expect(fromEvent(key("Digit1", { shiftKey: true, metaKey: true }))).toEqual({ text: "Meta+Shift+1" });
    expect(fromEvent(key("PageUp", { metaKey: true }))).toEqual({ text: "Meta+PgUp" });
    expect(fromEvent(key("Slash", { ctrlKey: true }))).toEqual({ text: "Ctrl+/" });
  });

  it("allows function keys alone", () => {
    expect(fromEvent(key("F12"))).toEqual({ text: "F12" });
  });

  it("asks for a modifier on typing keys", () => {
    expect(fromEvent(key("KeyN"))).toEqual({ error: "Add Meta, Ctrl or Alt" });
    expect(fromEvent(key("KeyN", { shiftKey: true }))).toEqual({ error: "Add Meta, Ctrl or Alt" });
  });

  it("waits while only a modifier is down", () => {
    expect(fromEvent(key("ControlLeft", { ctrlKey: true }))).toBeNull();
    expect(fromEvent(key("MetaLeft", { metaKey: true }))).toBeNull();
  });
});
