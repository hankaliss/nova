import React, { useMemo } from "react";
import { renderMarkdown } from "../lib/markdown";
import { hideToolArtifacts } from "../lib/artifacts";

export function Markdown({
  text,
  user,
  streaming,
}: {
  text: string;
  user?: boolean;
  /** Draw the caret inline after the last block (see .md-streaming). */
  streaming?: boolean;
}) {
  const html = useMemo(() => renderMarkdown(hideToolArtifacts(text)), [text]);
  return (
    <div
      className={`md ${user ? "md-user" : ""} ${streaming ? "md-streaming" : ""}`}
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}