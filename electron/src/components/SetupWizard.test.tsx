// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { SetupWizard, SetupStatus } from "./SetupWizard";
import { stubNova } from "../test/setup";

const base: SetupStatus = {
  ollama: { installed: true, reachable: true },
  gpu: { name: "RTX 2070", vram_total_mib: 8192 },
  models: [],
  recommended: { model: "qwen3:4b", note: "Benchmarked: runs fully on the GPU." },
  searxng: false,
  shortcut: "",
  onboarding_done: false,
  pulling: false,
};

function setup(status: Partial<SetupStatus> = {}, settings = { toggle_shortcut: "" }) {
  const { nova, emit } = stubNova();
  let current: SetupStatus = { ...base, ...status };
  nova.call.mockImplementation(async (method: string) => (method === "getSetupStatus" ? current : undefined));
  const onClose = vi.fn();
  const onSaveSettings = vi.fn();
  const view = render(
    <SetupWizard open settings={settings} onSaveSettings={onSaveSettings} onClose={onClose} />
  );
  const setStatus = (s: Partial<SetupStatus>) => {
    current = { ...current, ...s };
  };
  const calls = (method: string) => nova.call.mock.calls.filter((c: unknown[]) => c[0] === method);
  return { nova, emit, onClose, onSaveSettings, setStatus, calls, view };
}

afterEach(() => vi.useRealTimers());

describe("SetupWizard", () => {
  it("shows the start command while Ollama is down and moves on once it answers", async () => {
    vi.useFakeTimers();
    const { setStatus } = setup({ ollama: { installed: true, reachable: false } });
    await act(async () => {});
    expect(screen.getByText("Ollama is not running")).toBeTruthy();
    expect(screen.getByText("sudo systemctl enable --now ollama")).toBeTruthy();
    expect((screen.getByRole("button", { name: "Next" }) as HTMLButtonElement).disabled).toBe(true);

    setStatus({ ollama: { installed: true, reachable: true } });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2100);
    });
    expect(screen.getByText("Recommended")).toBeTruthy();
  });

  it("offers the install command when Ollama is missing", async () => {
    setup({ ollama: { installed: false, reachable: false } });
    expect(await screen.findByText("curl -fsSL https://ollama.com/install.sh | sh")).toBeTruthy();
  });

  it("downloads the recommended model with progress, then shows it ready", async () => {
    const { emit, calls } = setup();
    fireEvent.click(await screen.findByRole("button", { name: /Download/ }));
    expect(calls("pullModel")).toEqual([["pullModel", { name: "qwen3:4b" }]]);

    act(() => emit("pull_progress", { model: "qwen3:4b", status: "pulling", completed: 1.25e9, total: 2.5e9 }));
    expect(screen.getByRole("progressbar").getAttribute("aria-valuenow")).toBe("50");
    expect(screen.getByText(/1.3 GB \/ 2.5 GB · 50%/)).toBeTruthy();
    expect((screen.getByRole("button", { name: "Next" }) as HTMLButtonElement).disabled).toBe(true);

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(calls("cancelPull")).toHaveLength(1);

    await act(async () => emit("pull_done", { model: "qwen3:4b", ok: true, error: "" }));
    expect(screen.getAllByText("Ready")).toHaveLength(2); // step label + the model card
    expect(screen.queryByRole("button", { name: /Download/ })).toBeNull();
    expect(screen.queryByRole("progressbar")).toBeNull();
  });

  it("shows a readable error with Retry", async () => {
    const { emit, calls } = setup();
    fireEvent.click(await screen.findByRole("button", { name: /Download/ }));
    act(() => emit("pull_done", { model: "qwen3:4b", ok: false, error: "Not enough disk space for this model." }));
    expect(screen.getByRole("alert").textContent).toContain("Not enough disk space");
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(calls("pullModel")).toHaveLength(2);
  });

  it("lets you use a model you already have", async () => {
    const { calls } = setup({ models: ["qwen3:4b", "gemma4:e4b"] });
    fireEvent.click(await screen.findByRole("button", { name: "Use it" }));
    expect(calls("selectModel")).toEqual([["selectModel", { name: "qwen3:4b" }]]);
    fireEvent.click(screen.getByRole("button", { name: "gemma4:e4b" }));
    expect(calls("selectModel")[1]).toEqual(["selectModel", { name: "gemma4:e4b" }]);
  });

  it("walks to the end and marks setup done", async () => {
    const { calls, onClose, onSaveSettings } = setup({ searxng: true });
    await screen.findByText("Recommended");
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(screen.getByText("SearXNG found at localhost:8888")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(onSaveSettings).not.toHaveBeenCalled(); // shortcut unchanged
    expect(screen.getByText("Destructive")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Start chatting" }));
    expect(calls("markOnboardingDone")).toHaveLength(1);
    expect(onClose).toHaveBeenCalled();
  });

  it("closing counts as done", async () => {
    const { calls, onClose } = setup();
    await screen.findByText("Recommended");
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(calls("markOnboardingDone")).toHaveLength(1);
    expect(onClose).toHaveBeenCalled();
  });
});
