import { PendingChoices } from "./types";

/**
 * Client-side fallback for multiple-choice: the LLM sometimes writes the
 * options as plain text ("1. Yes / 2. No — just type 1 or 2") instead of
 * calling nova.presentChoices. When a completed assistant message contains a
 * run of 2-9 consecutively-numbered option lines and any invitation phrasing,
 * we extract the choices so the UI can render them as clickable buttons.
 */

// "1. x", "1) x", "1: x", "1️⃣ x", "1️⃣: x"
const OPTION_RE = /^\s*(\d{1,2})\s*[\uFE0F\u20E3.]*\s*(?:[.):\-]\s*)?(.+)$/;

// Only treat a list as a choice when the text invites picking one — avoids
// turning arbitrary numbered outlines/recipes into buttons.
const INVITE_RE =
  /choose|choice|choices|pick|select|selection|which|option|options?|type|press|either|number|proceed|answer|reply|let me know|tell me|\b\d\s*(?:-|,| or )\s*\d\b/i;

function parseOptionLine(line: string): { num: number; text: string } | null {
  const m = OPTION_RE.exec(line);
  if (!m) return null;
  return { num: parseInt(m[1], 10), text: m[2].trim() };
}

export function extractChoices(content: string): PendingChoices | null {
  if (!content) return null;
  const lines = content.split("\n");
  const parsed = lines.map(parseOptionLine);

  // Find the LAST run of consecutively-numbered option lines starting at 1.
  let start = -1;
  let end = -1;
  for (let i = 0; i < parsed.length; i++) {
    const p = parsed[i];
    if (!p || p.num !== 1) continue;
    let j = i + 1;
    while (j < parsed.length) {
      const n = parsed[j];
      if (!n || n.num !== j - i + 1) break;
      j++;
    }
    if (j - i >= 2) {
      start = i;
      end = j; // exclusive
    }
    i = j - 1; // skip the run we found
  }
  if (start < 0) return null;

  const options = parsed
    .slice(start, end)
    .map((p) => p!.text)
    .filter((t) => t.length > 0);
  if (options.length < 2 || options.length > 9) return null;

  const before = lines.slice(0, start).join(" ").replace(/\s+/g, " ").trim();
  const after = lines.slice(end).join(" ").replace(/\s+/g, " ").trim();
  if (!INVITE_RE.test(`${before} ${after}`)) return null;

  const question = before || "Choose one:";
  return {
    question: question.length > 600 ? `${question.slice(0, 597)}…` : question,
    options,
  };
}