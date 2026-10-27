// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { ArtifactPanel } from "./ArtifactPanel";
import { stubNova } from "../test/setup";
import type { Artifact } from "../lib/artifacts";

const art = (n: number, total = 3): Artifact => ({
  id: `a${n}`,
  title: `Clock v${n}`,
  type: "html",
  content: `<h1>rev ${n}</h1>`,
  group: "g",
  version: n,
  versions: total,
  tool: false,
});

function setup(at: number, versions = [art(1), art(2), art(3)]) {
  const { nova } = stubNova();
  const onSelect = vi.fn();
  const onClose = vi.fn();
  render(<ArtifactPanel artifact={versions[at]} versions={versions} onSelect={onSelect} onClose={onClose} />);
  return { nova, onSelect, onClose, versions };
}

describe("ArtifactPanel", () => {
  it("steps between versions", () => {
    const { onSelect, versions } = setup(1);
    expect(screen.getByText("v2 of 3")).toBeTruthy();
    fireEvent.click(screen.getByLabelText("Previous version"));
    expect(onSelect).toHaveBeenLastCalledWith(versions[0]);
    fireEvent.click(screen.getByLabelText("Next version"));
    expect(onSelect).toHaveBeenLastCalledWith(versions[2]);
  });

  it("disables the stepper at either end", () => {
    setup(0);
    expect((screen.getByLabelText("Previous version") as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByLabelText("Next version") as HTMLButtonElement).disabled).toBe(false);
  });

  it("hides the stepper for a single version", () => {
    setup(0, [art(1, 1)]);
    expect(screen.queryByLabelText("Previous version")).toBeNull();
  });

  it("strips the version suffix from the title when there are revisions", () => {
    setup(1);
    expect(screen.getByText("Clock")).toBeTruthy();
  });

  it("switches between preview and source", () => {
    setup(0);
    const frame = () => document.querySelector("iframe");
    expect(frame()?.getAttribute("srcdoc")).toBe("<h1>rev 1</h1>");
    fireEvent.click(screen.getByLabelText("Code"));
    expect(frame()).toBeNull();
    expect(screen.getByText("<h1>rev 1</h1>")).toBeTruthy();
    fireEvent.click(screen.getByLabelText("Preview"));
    expect(frame()).not.toBeNull();
  });

  it("copies the source, opens in the browser and closes", () => {
    const { nova, onClose } = setup(0);
    fireEvent.click(screen.getByTitle("Copy source code"));
    expect(nova.copyText).toHaveBeenCalledWith("<h1>rev 1</h1>");
    fireEvent.click(screen.getByTitle("Open in your browser"));
    expect(nova.openArtifact).toHaveBeenCalledWith("<h1>rev 1</h1>");
    fireEvent.click(screen.getByTitle("Close artifact"));
    expect(onClose).toHaveBeenCalled();
  });

  describe("resizing", () => {
    beforeEach(() => {
      // This jsdom has no usable localStorage; a Map stands in for it.
      const store = new Map<string, string>();
      vi.stubGlobal("localStorage", {
        getItem: (k: string) => store.get(k) ?? null,
        setItem: (k: string, v: string) => void store.set(k, String(v)),
        removeItem: (k: string) => void store.delete(k),
        clear: () => store.clear(),
      });
      window.innerWidth = 1200;
    });
    const panel = () => screen.getByRole("complementary", { name: "Artifact preview" });
    const handle = () => screen.getByRole("separator", { name: "Resize artifact panel" });

    it("starts at the default width and widens with the keyboard, remembering it", () => {
      setup(0);
      expect(panel().style.width).toBe("480px");
      fireEvent.keyDown(handle(), { key: "ArrowLeft" });
      expect(panel().style.width).toBe("512px");
      expect(localStorage.getItem("nova.artifactWidth")).toBe("512");
    });

    it("drags the left edge: leftwards is wider", () => {
      setup(0);
      fireEvent.pointerDown(handle(), { button: 0, clientX: 700, pointerId: 1 });
      fireEvent.pointerMove(handle(), { clientX: 500, pointerId: 1 });
      expect(panel().style.width).toBe("680px");
      fireEvent.pointerUp(handle(), { clientX: 400, pointerId: 1 });
      expect(panel().style.width).toBe("780px");
      expect(localStorage.getItem("nova.artifactWidth")).toBe("780");
    });

    it("keeps room for the chat and a minimum width", () => {
      localStorage.setItem("nova.artifactWidth", "5000");
      setup(0);
      expect(panel().style.width).toBe("840px"); // 1200 - 360 for the chat
      for (let i = 0; i < 40; i++) fireEvent.keyDown(handle(), { key: "ArrowRight" });
      expect(panel().style.width).toBe("320px");
    });

    it("double-click resets to the default", () => {
      localStorage.setItem("nova.artifactWidth", "700");
      setup(0);
      expect(panel().style.width).toBe("700px");
      fireEvent.doubleClick(handle());
      expect(panel().style.width).toBe("480px");
      expect(localStorage.getItem("nova.artifactWidth")).toBeNull();
    });
  });
});
