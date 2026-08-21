export interface Artifact {
  id: string;
  title: string;
  type: "html";
  content: string;
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
      });
      index++;
    }
  }

  return artifacts;
}
