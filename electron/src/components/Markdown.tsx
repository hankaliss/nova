import React, { useMemo } from "react";
import { renderMarkdown } from "../lib/markdown";

export function Markdown({ text, user }: { text: string; user?: boolean }) {
  const html = useMemo(() => renderMarkdown(text), [text]);
  return (
    <div
      className={`md ${user ? "md-user" : ""}`}
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}