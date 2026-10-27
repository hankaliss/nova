import type { ToolEvent } from "./types";

/**
 * Client-side fallback for skill creation: the LLM sometimes shows the
 * SKILL.md and tools.py content but never calls nova.skill.create. We detect
 * it however it appears and offer a "Save skill" button that calls the
 * createSkill RPC so the files actually land on disk.
 *
 * Detection strategies:
 *  1. Fenced code blocks labeled SKILL.md / tools.py (or markdown / python).
 *  2. The "run these commands" format: `echo "..." > .../SKILL.md` together
 *     with a tools.py redirect — the model's manual shell script.
 *  3. Heredoc form: `cat > .../SKILL.md <<'EOF'` ... `EOF`.
 * A bare module-level `Tool(...)` (no get_tools) is wrapped into a valid
 * `def get_tools(): return [...]` before saving.
 */

export interface DetectedSkill {
  name: string;
  skill_md: string;
  tools_py: string;
}

interface CodeBlock {
  label: string; // language / filename as written after ```
  content: string;
}

interface FilePair {
  skill_md?: string;
  tools_py?: string;
  name?: string;
}

// Extract ```lang ... ``` blocks from a message's raw content.
function codeBlocks(content: string): CodeBlock[] {
  const blocks: CodeBlock[] = [];
  const re = /```(?:\s*)([^\n`]*)\n?([\s\S]*?)```/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(content)) !== null) {
    blocks.push({ label: m[1].trim().toLowerCase(), content: m[2] });
  }
  return blocks;
}

// Turn a `# Heading` into a valid skill name: lowercase, spaces->underscores,
// strip anything that isn't [a-z0-9_].
export function nameFromHeading(heading: string): string {
  const cleaned = heading
    .replace(/^#+\s*/, "")
    .trim()
    .toLowerCase()
    .replace(/\s+/g, "_")
    .replace(/[^a-z0-9_]/g, "")
    .replace(/^_+|_+$/g, "");
  return cleaned || "";
}

// Is the block a SKILL.md file? Labeled as markdown/md, or unlabeled but
// starts with a `# ` heading that looks like a skill.
function isMdBlock(b: CodeBlock): boolean {
  const label = b.label.replace(/`/g, "").trim();
  if (/^skill(\s+md|\.md)?$|^markdown$|^md$/.test(label)) return true;
  if (!label && /^#\s+[A-Za-z]/.test(b.content.trim())) return true;
  return false;
}

// Is the block a tools.py file? Labeled python/py, or unlabeled but contains
// a get_tools definition or a bare `Tool(...)` definition.
function isPyBlock(b: CodeBlock): boolean {
  const label = b.label.replace(/`/g, "").trim();
  if (/^tools?(\s+\.py|\.py)?$|^python$|^py$/.test(label)) return true;
  if (!label && /\bdef\s+get_tools\s*\(/.test(b.content)) return true;
  if (!label && /^\s*Tool\s*\(/.test(b.content.trim())) return true;
  return false;
}

function unescape(s: string): string {
  return s
    .replace(/\\n/g, "\n")
    .replace(/\\t/g, "\t")
    .replace(/\\"/g, '"')
    .replace(/\\'/g, "'")
    .replace(/\\\\/g, "\\");
}

const FILEPATH_RE = /\/([a-z][a-z0-9_]*)\/(SKILL\.md|tools\.py)$/i;
const MK_DIR_RE = /mkdir\s+-p\s+.*\/skills\/([a-z][a-z0-9_]*)/i;

function foldFile(out: FilePair, dest: string, body: string): void {
  const fname = /(SKILL\.md|tools\.py)$/i.exec(dest);
  if (!fname) return;
  if (fname[1].toLowerCase() === "skill.md") out.skill_md = body;
  else out.tools_py = body;
  if (!out.name) {
    const dir = FILEPATH_RE.exec(dest);
    if (dir) out.name = dir[1].toLowerCase();
  }
}

function nameFromMkdir(content: string): string | undefined {
  const m = MK_DIR_RE.exec(content);
  return m ? m[1].toLowerCase() : undefined;
}

// Strategy 2: pull SKILL.md/tools.py out of `echo "..." > ./skills/<n>/FILE`
// commands (what the model prints when it tells the user to run things by hand).
function parseEchoCommands(content: string): FilePair | null {
  const out: FilePair = {};
  for (const line of content.split("\n")) {
    const m = line.match(/echo\s+"((?:\\.|[^"\\])*)"\s*>\s*(\S+)/);
    if (!m) continue;
    foldFile(out, m[2], unescape(m[1]));
  }
  if (!out.name) out.name = nameFromMkdir(content);
  return out.skill_md && out.tools_py ? out : null;
}

// Strategy 3: `cat > ./skills/<n>/FILE <<'EOF' ... EOF` heredoc form.
function parseHeredocs(content: string): FilePair | null {
  const out: FilePair = {};
  const re = /cat\s*>\s*(\S+)\s*<<\s*['"]?([A-Za-z0-9_]+)['"]?\s*\n([\s\S]*?)\n?\s*\2/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(content)) !== null) {
    foldFile(out, m[1], m[3]);
  }
  if (!out.name) out.name = nameFromMkdir(content);
  return out.skill_md && out.tools_py ? out : null;
}

// Normalize a bare module-level Tool(...) into a get_tools() function so a
// one-liner the model wrote for `echo` becomes a valid tools.py.
export function ensureGetTools(tools_py: string): string {
  const trimmed = tools_py.trim();
  if (/\bdef\s+get_tools\s*\(/.test(trimmed)) return tools_py;
  const body = trimmed.replace(/^from\s+nova\.tools\.tool\s+import[^\n]*\n?/, "");
  if (!/^\s*Tool\s*\(/.test(body)) return tools_py;
  const indent = (s: string) =>
    s
      .split("\n")
      .map((l) => "    " + l)
      .join("\n");
  return (
    "from nova.tools.tool import Tool, RiskLevel\n" +
    "\n" +
    "def get_tools():\n" +
    "    return [\n" +
    indent(body) +
    "\n" +
    "    ]\n"
  );
}

function plausibleSkill(skill_md: string, tools_py: string): boolean {
  return /^#\s+.+/m.test(skill_md) && /Tool\s*\(/.test(tools_py);
}

// The skill-name prefix used by the tools in tools.py (the code is the ground
// truth the backend validates against): `Tool(name="weather.get"...` → weather.
export function toolPrefixOf(tools_py: string): string {
  const m =
    /name\s*=\s*['"]([a-z][a-z0-9_]*)\./.exec(tools_py) ||
    /Tool\(\s*['"]([a-z][a-z0-9_]*)\./.exec(tools_py);
  return m ? m[1] : "";
}

// Rewrite every Tool name prefix to match the chosen skill name. The model
// often writes a tool prefix that disagrees with the heading/path/typed name
// (e.g. tool `system_updates.check` under name `pacman_uptade`); the backend
// rejects those, so align them so the save always validates.
export function alignToolNames(tools_py: string, name: string): string {
  if (!/^[a-z][a-z0-9_]*$/.test(name)) return tools_py;
  return tools_py
    .replace(/name\s*=\s*(['"])([a-z][a-z0-9_]*)\./g, (m, q) => `name=${q}${name}.`)
    .replace(/Tool\(\s*(['"])([a-z][a-z0-9_]*)\./g, (m, q) => `Tool(${q}${name}.`);
}

// Build a DetectedSkill from extracted files. The skill name prefers the
// tools.py tool prefix (code-authoritative), then the command-path hint, then
// the SKILL.md heading; the returned tools_py is aligned to that name.
function build(
  skill_md: string,
  tools_py: string,
  nameHint = ""
): DetectedSkill | null {
  const md = skill_md.trim();
  const py = ensureGetTools(tools_py.trim());
  if (!plausibleSkill(md, py)) return null;

  let name = toolPrefixOf(py);
  if (!name) name = /^[a-z][a-z0-9_]*$/.test(nameHint) ? nameHint : "";
  if (!name) {
    const headingLine = md.split("\n").find((l) => /^#\s+/.test(l)) || "";
    name = nameFromHeading(headingLine);
  }
  return { name, skill_md: md, tools_py: name ? alignToolNames(py, name) : py };
}

// The skill was already created if this message carried a nova.skill.create
// tool call (success or failure) — don't offer a second save.
export function alreadyHasSkillResult(tools: ToolEvent[]): boolean {
  return tools.some((t) => t.name === "nova.skill.create");
}

export function extractSkill(
  content: string,
  tools: ToolEvent[] = []
): DetectedSkill | null {
  if (!content || alreadyHasSkillResult(tools)) return null;

  // Strategy 1: fenced code blocks (SKILL.md + tools.py).
  const blocks = codeBlocks(content);
  const md = blocks.find(isMdBlock);
  const py = blocks.find(isPyBlock);
  if (md && py) {
    const hit = build(md.content, py.content);
    if (hit) return hit;
  }

  // Strategy 2/3: manual-command formats (echo redirects, heredocs).
  const pair = parseEchoCommands(content) || parseHeredocs(content);
  if (pair) {
    const hit = build(pair.skill_md!, pair.tools_py!, pair.name);
    if (hit) return hit;
  }

  return null;
}