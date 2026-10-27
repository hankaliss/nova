import { describe, expect, it } from "vitest";
import { toolKind } from "./toolKinds";

describe("toolKind", () => {
  it.each([
    ["web.search", "Web"],
    ["web.fetch", "Web"],
    ["linux.processes", "System"],
    ["services.restart", "Services"],
    ["filesystem.read", "Files"],
    ["shell.execute", "Shell"],
    ["memory.remember", "Memory"],
    ["mcp.github.get_me", "MCP github"],
    ["nova.presentChoices", "Question"],
    ["nova.skill.create", "Skill maker"],
    ["system_doctor.check", "Skill"],
    ["oddtool", "Tool"],
  ])("%s is %s", (name, label) => {
    expect(toolKind(name).label).toBe(label);
  });
});
