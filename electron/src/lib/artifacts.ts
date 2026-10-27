export interface Artifact {
  id: string;
  title: string;
  type: "html";
  content: string;
  /** Revisions of one artifact share a group (see indexArtifacts). */
  group: string;
  /** 1-based revision number within the group, and the group's size. */
  version: number;
  versions: number;
  /** Rendered by a tool (skill report), not written by the model (AD-039). */
  tool: boolean;
}

/** Marks a tool-rendered artifact in a saved reply (nova/tools/report.py). */
export const TOOL_ARTIFACT_MARKER = "<!-- nova:artifact -->";
const TOOL_ARTIFACT_BLOCK = /\n*```html\n<!-- nova:artifact -->[\s\S]*?(?:\n```|$)\n*/g;

/** Reply text without tool artifacts' code: they show as a badge only. */
export function hideToolArtifacts(text: string): string {
  return text.replace(TOOL_ARTIFACT_BLOCK, "\n\n");
}

function extractTitle(html: string): string {
  // 1. Check <title>...</title>
  const titleMatch = html.match(/<title[^>]*>([^<]+)<\/title>/i);
  if (titleMatch && titleMatch[1].trim()) {
    return titleMatch[1].trim();
  }

  // 2. Check <!-- title: ... --> or <!-- artifact: ... -->
  const commentMatch = html.match(/<!--\s*(?:title|artifact):\s*([^\n-]+)\s*-->/i);
  if (commentMatch && commentMatch[1].trim()) {
    return commentMatch[1].trim();
  }

  // 3. Check first <h1>...</h1> or <h2>...</h2>
  const headingMatch = html.match(/<h[12][^>]*>([^<]+)<\/h[12]>/i);
  if (headingMatch && headingMatch[1].trim()) {
    return headingMatch[1].trim();
  }

  return "HTML Artifact";
}

function isCompleteHtml(code: string): boolean {
  const trimmed = code.trim();
  if (
    trimmed.includes("<!DOCTYPE") ||
    trimmed.includes("<html") ||
    trimmed.includes("<body") ||
    trimmed.includes("<script") ||
    trimmed.includes("<style") ||
    trimmed.includes("<svg") ||
    trimmed.includes("<canvas")
  ) {
    return true;
  }
  // If it has multiple tags and decent length
  return (
    trimmed.length > 80 &&
    (trimmed.match(/<[a-z0-9-]+[^>]*>/gi) || []).length >= 3
  );
}

export function extractArtifacts(text: string, messageId: string | number): Artifact[] {
  if (!text) return [];

  const artifacts: Artifact[] = [];
  // Match ```html ... ``` or ```xml ... ``` or ```svg ... ```
  const codeBlockRegex = /```(?:html|htm|svg|xml)?\s*\n([\s\S]*?)```/gi;
  let match: RegExpExecArray | null;
  let index = 0;

  while ((match = codeBlockRegex.exec(text)) !== null) {
    const rawCode = match[1].trim();
    if (isCompleteHtml(rawCode)) {
      const title = extractTitle(rawCode);
      artifacts.push({
        id: `art-${messageId}-${index}`,
        title: title || `Artifact #${index + 1}`,
        type: "html",
        content: rawCode,
        group: groupKey(title),
        version: 1,
        versions: 1,
        tool: rawCode.startsWith(TOOL_ARTIFACT_MARKER),
      });
      index++;
    }
  }

  return artifacts;
}

const VERSION_SUFFIX = /\s+v\d+(\.\d+)*$/i;

/** Revisions keep their title, sometimes with a "v2"-style suffix. */
function groupKey(title: string): string {
  return title.replace(VERSION_SUFFIX, "").trim().toLowerCase();
}

/** Title to show: with several revisions the version is shown separately. */
export function displayTitle(art: Artifact): string {
  return art.versions > 1 ? art.title.replace(VERSION_SUFFIX, "").trim() || art.title : art.title;
}

export interface ArtifactIndex {
  byMessage: Map<number, Artifact[]>;
  byId: Map<string, Artifact>;
  /** Each group's revisions, oldest first. */
  groups: Map<string, Artifact[]>;
}

/** All artifacts of a conversation, numbered as revisions of their group. */
export function indexArtifacts(
  messages: { role: string; content: string }[]
): ArtifactIndex {
  const byMessage = new Map<number, Artifact[]>();
  const byId = new Map<string, Artifact>();
  const groups = new Map<string, Artifact[]>();
  messages.forEach((message, idx) => {
    if (message.role !== "assistant" || !message.content) return;
    const found = extractArtifacts(message.content, idx);
    if (found.length === 0) return;
    byMessage.set(idx, found);
    for (const art of found) {
      const list = groups.get(art.group) ?? [];
      list.push(art);
      groups.set(art.group, list);
      byId.set(art.id, art);
    }
  });
  for (const list of groups.values()) {
    list.forEach((art, i) => {
      art.version = i + 1;
      art.versions = list.length;
    });
  }
  return { byMessage, byId, groups };
}
