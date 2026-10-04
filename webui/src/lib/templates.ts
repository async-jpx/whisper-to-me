/* Template rules shared by the Templates page and the composer's picker.
   validateTemplate mirrors templates.create_template on the daemon so most
   mistakes are caught before the request; the server stays the authority. */

import type { NewTemplate, Template } from "../api/types";

export const MAX_BODY_CHARS = 20_000;

/* A body the summarizer reads as instructions, one "## Section" per block.
   It already satisfies the Action Items invariant. */
export const TEMPLATE_SKELETON = `## TL;DR
2-3 sentences: what the meeting was about and what came out of it.

## Key Points
The main topics discussed, as short bullets.

## Decisions
What was agreed, as a bulleted list.

## Action Items
Bullets in the form "- [ ] task — owner (due date)"; omit owner/due when not
given.
`;

/* The file name the daemon derives from a display name. */
export function templateSlug(name: string): string {
  return name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 64)
    .replace(/^-+|-+$/g, "");
}

export function validateTemplate(draft: NewTemplate, existing: readonly Template[]): string | null {
  const slug = templateSlug(draft.name);
  if (!slug) return "Give the template a name with at least one letter or digit.";
  if (existing.some((t) => t.name === slug)) return `A template named “${slug}” already exists.`;
  const body = draft.body.trim();
  if (body.length > MAX_BODY_CHARS) return `Keep the body under ${MAX_BODY_CHARS} characters.`;
  if (!body.includes("## Action Items")) return "The body needs a “## Action Items” section.";
  if (!body.includes("- [ ]")) return "The Action Items section needs a “- [ ]” task line.";
  return null;
}

/* Favorites first; otherwise the daemon's order (built-ins, then yours). */
export function sortTemplates(templates: readonly Template[]): Template[] {
  return [...templates].sort((a, b) => Number(b.favorite) - Number(a.favorite));
}

export function templateLabel(t: Pick<Template, "name" | "title">): string {
  return t.title || t.name;
}

/* The "## Heading" lines of a body, for a compact preview. */
export function sectionTitles(body: string): string[] {
  return [...body.matchAll(/^##\s+(.+?)\s*$/gm)].map((m) => m[1] ?? "").filter(Boolean);
}
