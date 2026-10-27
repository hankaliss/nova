// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { PermissionDialog } from "./PermissionDialog";
import { stubNova } from "../test/setup";

const req = (over = {}) => ({
  tool: "linux.gpu_info",
  risk: "read",
  permission: "linux.read",
  args: "{}",
  ...over,
});

function setup(request: ReturnType<typeof req> | null) {
  stubNova();
  const onRespond = vi.fn();
  render(<PermissionDialog request={request} onRespond={onRespond} />);
  return onRespond;
}

describe("PermissionDialog", () => {
  it("renders nothing without a request", () => {
    setup(null);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("approves and denies", () => {
    const onRespond = setup(req());
    fireEvent.click(screen.getByText("Approve"));
    expect(onRespond).toHaveBeenLastCalledWith(true, false);
    fireEvent.click(screen.getByText("Deny"));
    expect(onRespond).toHaveBeenLastCalledWith(false, false);
  });

  it("Escape denies", () => {
    const onRespond = setup(req());
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onRespond).toHaveBeenCalledWith(false, false);
  });

  it("remembers the grant when 'Always allow' is on", () => {
    const onRespond = setup(req({ risk: "write" }));
    fireEvent.click(screen.getByRole("switch", { name: "Always allow this tool" }));
    fireEvent.click(screen.getByText("Approve"));
    expect(onRespond).toHaveBeenCalledWith(true, true);
  });

  it("never offers to remember a destructive tool", () => {
    const onRespond = setup(req({ risk: "destructive" }));
    expect(screen.getByText("This can't be undone")).toBeTruthy();
    expect(screen.queryByRole("switch")).toBeNull();
    fireEvent.click(screen.getByText("Approve"));
    expect(onRespond).toHaveBeenCalledWith(true, false);
  });

  it("shows the arguments pretty-printed", () => {
    setup(req({ args: '{"path":"/tmp/x"}' }));
    expect(screen.getByText("What it will run with")).toBeTruthy();
    expect(screen.getByText(/"path": "\/tmp\/x"/)).toBeTruthy();
  });

  it("previews the code of a skill being created", () => {
    setup(
      req({
        tool: "nova.skill.create",
        risk: "destructive",
        args: JSON.stringify({ name: "demo", skill_md: "# d\nline", tools_py: "print(1)\nprint(2)" }),
      })
    );
    expect(screen.getByText("Create a new skill")).toBeTruthy();
    expect(screen.getByText("demo")).toBeTruthy();
    expect(screen.getByText(/SKILL\.md \(2 lines\), tools\.py \(2 lines\)/)).toBeTruthy();
    expect(screen.getByText(/print\(1\)/)).toBeTruthy();
  });
});
