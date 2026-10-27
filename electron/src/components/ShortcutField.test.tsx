// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { ShortcutField } from "./ShortcutField";
import { stubNova } from "../test/setup";

const ok = { available: true, shortcut: "Meta+Space", error: "" };

function setup(props: Partial<Parameters<typeof ShortcutField>[0]> = {}) {
  const { nova } = stubNova();
  const onChange = vi.fn();
  render(
    <ShortcutField value="Meta+Space" savedValue="Meta+Space" status={ok} onChange={onChange} {...props} />
  );
  return { nova, onChange };
}

const record = () => fireEvent.click(screen.getByRole("button", { name: /click to change|Set a shortcut/ }));
const press = (init: KeyboardEventInit) => act(() => void fireEvent.keyDown(window, init));

describe("ShortcutField", () => {
  it("shows the active status", () => {
    setup();
    expect(screen.getByText(/Active in KDE/)).toBeTruthy();
  });

  it("says when the shortcut service is missing", () => {
    setup({ status: { available: false, shortcut: "", error: "" } });
    expect(screen.getByText("Needs KDE Plasma's shortcut service")).toBeTruthy();
  });

  it("flags an unsaved change", () => {
    setup({ value: "Ctrl+Alt+N" });
    expect(screen.getByText("Applied when you save")).toBeTruthy();
  });

  it("shows a registration error", () => {
    setup({ status: { ...ok, error: "already taken" } });
    expect(screen.getByText("already taken")).toBeTruthy();
  });

  it("records a combination, reports it and tells the main process", () => {
    const { nova, onChange } = setup();
    record();
    expect(screen.getByText("Press the keys, Escape to cancel")).toBeTruthy();
    expect(nova.call).toHaveBeenCalledWith("shortcutRecording", { active: true });
    press({ code: "KeyN", key: "n", ctrlKey: true, altKey: true });
    expect(onChange).toHaveBeenCalledWith("Ctrl+Alt+N");
    expect(nova.call).toHaveBeenLastCalledWith("shortcutRecording", { active: false });
  });

  it("Escape cancels without changing the value", () => {
    const { nova, onChange } = setup();
    record();
    press({ code: "Escape", key: "Escape" });
    expect(onChange).not.toHaveBeenCalled();
    expect(screen.queryByText("Press keys…")).toBeNull();
    expect(nova.call).toHaveBeenLastCalledWith("shortcutRecording", { active: false });
  });

  it("keeps recording and explains when the key is not allowed", () => {
    const { onChange } = setup();
    record();
    press({ code: "KeyN", key: "n" }); // no modifier
    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByText("Press keys…")).toBeTruthy();
  });

  it("Clear empties the value", () => {
    const { onChange } = setup();
    fireEvent.click(screen.getByText("Clear"));
    expect(onChange).toHaveBeenCalledWith("");
  });

  it("offers no Clear when nothing is set", () => {
    setup({ value: "", savedValue: "" });
    expect(screen.queryByText("Clear")).toBeNull();
    expect(screen.getByText("None")).toBeTruthy();
  });
});
