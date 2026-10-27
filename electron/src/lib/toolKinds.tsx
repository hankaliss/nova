import {
  Blocks,
  Brain,
  Cpu,
  FolderOpen,
  Globe,
  ListChecks,
  Plug,
  Server,
  SquareTerminal,
  Wrench,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

export interface ToolKind {
  label: string;
  Icon: LucideIcon;
  /** Tailwind text color for the icon and label. */
  color: string;
}

// Built-in tool families (src/nova/tools/). Anything else is a skill's tool
// ("<skill>.<tool>"), except MCP tools ("mcp.<server>.<tool>").
const BUILTIN: Record<string, ToolKind> = {
  web: { label: "Web", Icon: Globe, color: "text-lavender" },
  linux: { label: "System", Icon: Cpu, color: "text-meta" },
  services: { label: "Services", Icon: Server, color: "text-meta" },
  filesystem: { label: "Files", Icon: FolderOpen, color: "text-meta" },
  shell: { label: "Shell", Icon: SquareTerminal, color: "text-warn" },
  memory: { label: "Memory", Icon: Brain, color: "text-meta" },
};

export function toolKind(name: string): ToolKind {
  const [family, second] = name.split(".");
  if (BUILTIN[family]) return BUILTIN[family];
  if (family === "mcp") return { label: `MCP ${second ?? ""}`.trim(), Icon: Plug, color: "text-meta" };
  if (name === "nova.presentChoices") return { label: "Question", Icon: ListChecks, color: "text-meta" };
  if (name.startsWith("nova.skill.")) return { label: "Skill maker", Icon: Blocks, color: "text-accent" };
  // The tool name already starts with the skill name.
  if (family && second) return { label: "Skill", Icon: Blocks, color: "text-accent" };
  return { label: "Tool", Icon: Wrench, color: "text-meta" };
}
