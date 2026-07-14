/* Markdown-editing primitives shared by the note editor and the live
   scratchpad — a verbatim port of the old app.js textarea logic. All
   mutations go through editorInsert (execCommand is deprecated but still the
   only undo-stack-preserving path); the setRangeText fallback re-fires
   "input" so React onInput/onChange handlers still run. */

/* "- ", "* ", "1. ", "- [ ] "… — the prefixes the editor auto-continues. */
export const LIST_PREFIX_RE = /^(\s*)([-*+]|\d+[.)])(\s+)(\[[ xX]\]\s+)?/;

/* Insert text at the caret of `ta`, keeping the browser's undo stack when
   possible. */
export function editorInsert(ta: HTMLTextAreaElement, text: string): void {
  ta.focus();
  let done = false;
  try {
    done = text
      ? document.execCommand("insertText", false, text)
      : document.execCommand("delete", false);
  } catch {
    done = false;
  }
  if (!done) {
    ta.setRangeText(text, ta.selectionStart, ta.selectionEnd, "end");
    ta.dispatchEvent(new Event("input", { bubbles: true }));
  }
}

function currentLineBounds(ta: HTMLTextAreaElement): [number, number] {
  const start = ta.value.lastIndexOf("\n", ta.selectionStart - 1) + 1;
  const nl = ta.value.indexOf("\n", ta.selectionStart);
  return [start, nl === -1 ? ta.value.length : nl];
}

/* Toggles `mark` (e.g. "**" / "*") around the selection: wraps plain text,
   unwraps when the selection is already wrapped or sits just inside the
   marks — so Bold twice is a no-op, not "****text****". */
export function toggleWrap(ta: HTMLTextAreaElement, mark: string): void {
  const start = ta.selectionStart;
  const end = ta.selectionEnd;
  const sel = ta.value.slice(start, end);
  if (sel.length >= mark.length * 2 && sel.startsWith(mark) && sel.endsWith(mark)) {
    const inner = sel.slice(mark.length, sel.length - mark.length);
    editorInsert(ta, inner);
    ta.selectionStart = start;
    ta.selectionEnd = start + inner.length;
    return;
  }
  if (
    start >= mark.length &&
    ta.value.slice(start - mark.length, start) === mark &&
    ta.value.slice(end, end + mark.length) === mark
  ) {
    ta.selectionStart = start - mark.length;
    ta.selectionEnd = end + mark.length;
    editorInsert(ta, sel);
    ta.selectionStart = start - mark.length;
    ta.selectionEnd = start - mark.length + sel.length;
    return;
  }
  editorInsert(ta, mark + sel + mark);
  ta.selectionStart = start + mark.length;
  ta.selectionEnd = start + mark.length + sel.length;
}

/* All lines touched by the current selection (not just the caret line) —
   list toggling applies to every selected line, like Notion/Obsidian. */
function selectedLineBounds(ta: HTMLTextAreaElement): [number, number] {
  const start = ta.value.lastIndexOf("\n", ta.selectionStart - 1) + 1;
  const end = ta.value.indexOf("\n", ta.selectionEnd);
  return [start, end === -1 ? ta.value.length : end];
}

export type ListKind = "bullet" | "ordered" | "task";

function lineHasMarkerKind(line: string, kind: ListKind): boolean {
  const m = line.match(LIST_PREFIX_RE);
  if (!m) return false;
  if (kind === "task") return !!m[4];
  if (kind === "ordered") return /^\d+[.)]$/.test(m[2] ?? "");
  return /^[-*+]$/.test(m[2] ?? "") && !m[4];
}

/* Toggles a bullet / numbered / checklist marker on every selected line —
   stripping it if every line already has that kind, else applying it
   (replacing any other list marker so lines don't end up double-prefixed). */
export function toggleListMarker(ta: HTMLTextAreaElement, kind: ListKind): void {
  const [start, end] = selectedLineBounds(ta);
  const lines = ta.value.slice(start, end).split("\n");
  const allHaveKind = lines.every((line) => !line.trim() || lineHasMarkerKind(line, kind));
  const next = lines
    .map((line, i) => {
      if (!line.trim()) return line;
      const m = line.match(LIST_PREFIX_RE);
      const indent = m ? (m[1] ?? "") : (line.match(/^\s*/) || [""])[0] ?? "";
      const rest = m ? line.slice(m[0].length) : line.slice(indent.length);
      if (allHaveKind) return indent + rest;
      if (kind === "task") return `${indent}- [ ] ${rest}`;
      if (kind === "ordered") return `${indent}${i + 1}. ${rest}`;
      return `${indent}- ${rest}`;
    })
    .join("\n");
  ta.selectionStart = start;
  ta.selectionEnd = end;
  editorInsert(ta, next);
  ta.selectionStart = start;
  ta.selectionEnd = start + next.length;
}

/* Markdown typing niceties, shared by the note editor and the scratchpad:
   Cmd/Ctrl+B/I toggle bold/italic; Enter continues lists and task items
   (Enter on an empty item ends the list); Tab / Shift+Tab indent and outdent
   list items. Attach as the textarea's onKeyDown. */
export function markdownKeydown(evt: React.KeyboardEvent<HTMLTextAreaElement>): void {
  const ta = evt.currentTarget;

  if ((evt.metaKey || evt.ctrlKey) && (evt.key === "b" || evt.key === "i")) {
    evt.preventDefault();
    toggleWrap(ta, evt.key === "b" ? "**" : "*");
    return;
  }

  if (evt.metaKey || evt.ctrlKey || evt.altKey) return;

  if (evt.key === "Enter" && !evt.shiftKey && ta.selectionStart === ta.selectionEnd) {
    const [lineStart] = currentLineBounds(ta);
    const line = ta.value.slice(lineStart, ta.selectionStart);
    const m = line.match(LIST_PREFIX_RE);
    if (!m) return;
    evt.preventDefault();
    if (!line.slice(m[0].length)) {
      ta.selectionStart = lineStart; // empty item: remove the marker
      editorInsert(ta, "");
      return;
    }
    let marker = m[2] ?? "";
    const num = marker.match(/^(\d+)([.)])$/);
    if (num) marker = `${Number(num[1]) + 1}${num[2]}`;
    editorInsert(ta, "\n" + (m[1] ?? "") + marker + (m[3] ?? "") + (m[4] ? "[ ] " : ""));
    return;
  }

  if (evt.key === "Tab") {
    evt.preventDefault();
    const caret = ta.selectionStart;
    const [lineStart, lineEnd] = currentLineBounds(ta);
    const line = ta.value.slice(lineStart, lineEnd);
    if (LIST_PREFIX_RE.test(line)) {
      if (evt.shiftKey) {
        const removed = Math.min(2, ((line.match(/^ */) || [""])[0] ?? "").length);
        if (removed === 0) return;
        ta.selectionStart = lineStart;
        ta.selectionEnd = lineStart + removed;
        editorInsert(ta, "");
        const pos = Math.max(lineStart, caret - removed);
        ta.selectionStart = ta.selectionEnd = pos;
      } else {
        ta.selectionStart = ta.selectionEnd = lineStart;
        editorInsert(ta, "  ");
        ta.selectionStart = ta.selectionEnd = caret + 2;
      }
    } else if (!evt.shiftKey) {
      editorInsert(ta, "  ");
    }
  }
}
