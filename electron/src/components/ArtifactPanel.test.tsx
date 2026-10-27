// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
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
});
