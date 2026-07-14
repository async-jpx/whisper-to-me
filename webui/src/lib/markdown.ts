/* Shared markdown-it instance — same config as the old UI: html:false keeps
   raw HTML in notes escaped (content comes from speech and a local LLM, never
   trusted as markup); breaks:true gives single-newline transcript lines their
   own visual lines. Versions match the previously vendored files exactly so
   the rendered checkbox order keeps matching the server's task regex. */

import MarkdownIt from "markdown-it";
// @ts-expect-error — no type definitions shipped; the plugin's runtime shape
// (a markdown-it plugin function) is all we rely on.
import taskLists from "markdown-it-task-lists";

export const md: MarkdownIt = new MarkdownIt({
  html: false,
  linkify: false,
  breaks: true,
}).use(taskLists, { enabled: true });

/* Notes carry YAML frontmatter for Obsidian; it is metadata, not prose —
   hide it from rendered views (Edit/Copy still see the raw markdown). */
export function stripFrontmatter(mdText: string): string {
  if (!mdText.startsWith("---\n")) return mdText;
  const end = mdText.indexOf("\n---\n", 4);
  if (end === -1) return mdText;
  return mdText.slice(end + 5).replace(/^\n+/, "");
}

export function noteTitleFromMd(mdText: string): string | null {
  const m = stripFrontmatter(mdText).match(/^#\s+(.+)$/m);
  return m && m[1] ? m[1].trim() : null;
}
