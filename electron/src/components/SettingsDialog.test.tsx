// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { SettingsDialog } from "./SettingsDialog";
import { stubNova } from "../test/setup";
import type { Settings } from "../lib/types";

const settings = {
  close_to_tray: "true",
  auto_approve_writes: "false",
  always_on_top: "false",
  reduce_motion: "false",
  toggle_shortcut: "Meta+Space",
  temperature: "0.7",
  num_ctx: "auto",
  ollama_url: "http://localhost:11434",
  system_prompt: "Be brief",
  system_prompt_source: "bundled",
} as unknown as Settings;

function setup(open = true) {
  const { emit } = stubNova();
  const onSave = vi.fn();
  const onClose = vi.fn();
  const view = render(<SettingsDialog open={open} settings={settings} onSave={onSave} onClose={onClose} />);
  return { emit, onSave, onClose, view };
}

describe("SettingsDialog", () => {
  it("renders nothing while closed", () => {
    setup(false);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("saves the edited draft, not the original", () => {
    const { onSave } = setup();
    fireEvent.click(screen.getByRole("switch", { name: "Always on top" }));
    fireEvent.click(screen.getByText("Save"));
    expect(onSave).toHaveBeenCalledTimes(1);
    expect(onSave.mock.calls[0][0]).toMatchObject({ always_on_top: "true", close_to_tray: "true" });
    expect(settings.always_on_top).toBe("false");
  });

  it("turning a switch off stores 'false'", () => {
    const { onSave } = setup();
    fireEvent.click(screen.getByRole("switch", { name: "Close to tray" }));
    fireEvent.click(screen.getByText("Save"));
    expect(onSave.mock.calls[0][0].close_to_tray).toBe("false");
  });

  it("Cancel closes without saving", () => {
    const { onSave, onClose } = setup();
    fireEvent.click(screen.getByText("Cancel"));
    expect(onClose).toHaveBeenCalled();
    expect(onSave).not.toHaveBeenCalled();
  });

  it("edits model settings on the Model tab", () => {
    const { onSave } = setup();
    fireEvent.click(screen.getByRole("tab", { name: "Model" }));
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "8192" } });
    fireEvent.change(screen.getByPlaceholderText(/custom system prompt/), { target: { value: "Be long" } });
    fireEvent.click(screen.getByText("Save"));
    expect(onSave.mock.calls[0][0]).toMatchObject({ num_ctx: "8192", system_prompt: "Be long" });
  });

  it("reflects shortcut status events from the sidecar", () => {
    const { emit } = setup();
    act(() => emit("shortcut_status", { available: false, shortcut: "", error: "" }));
    expect(screen.getByText("Needs KDE Plasma's shortcut service")).toBeTruthy();
  });

  it("restarts from the saved settings when reopened", () => {
    const { view, onSave, onClose } = setup();
    fireEvent.click(screen.getByRole("switch", { name: "Always on top" }));
    view.rerender(<SettingsDialog open={false} settings={settings} onSave={onSave} onClose={onClose} />);
    view.rerender(<SettingsDialog open settings={settings} onSave={onSave} onClose={onClose} />);
    expect(screen.getByRole("switch", { name: "Always on top" }).getAttribute("aria-checked")).toBe("false");
  });
});
