/* Markdown-editing commands shared by the note editor and the live
   scratchpad, ported from the old textarea logic onto CodeMirror 6
   transactions (native undo history — no execCommand). Semantics match the
   old editor exactly: Cmd/Ctrl+B/I toggle bold/italic, Enter continues
   lists and task items (Enter on an empty item ends the list), Tab /
   Shift+Tab indent and outdent list items by two spaces. */

import { EditorSelection, Prec } from "@codemirror/state";
import { keymap, type Command, type EditorView } from "@codemirror/view";

/* "- ", "* ", "1. ", "- [ ] "… — the prefixes the editor auto-continues. */
export const LIST_PREFIX_RE = /^(\s*)([-*+]|\d+[.)])(\s+)(\[[ xX]\]\s+)?/;

/* Toggles `mark` (e.g. "**" / "*") around the selection: wraps plain text,
   unwraps when the selection is already wrapped or sits just inside the
   marks — so Bold twice is a no-op, not "****text****". */
export function toggleWrap(view: EditorView, mark: string): void {
  const { state } = view;
  const { from, to } = state.selection.main;
  const sel = state.sliceDoc(from, to);
  view.focus();
  if (sel.length >= mark.length * 2 && sel.startsWith(mark) && sel.endsWith(mark)) {
    const inner = sel.slice(mark.length, sel.length - mark.length);
    view.dispatch({
      changes: { from, to, insert: inner },
      selection: EditorSelection.range(from, from + inner.length),
      userEvent: "input",
    });
    return;
  }
  if (
    from >= mark.length &&
    state.sliceDoc(from - mark.length, from) === mark &&
    state.sliceDoc(to, to + mark.length) === mark
  ) {
    view.dispatch({
      changes: { from: from - mark.length, to: to + mark.length, insert: sel },
      selection: EditorSelection.range(from - mark.length, from - mark.length + sel.length),
      userEvent: "input",
    });
    return;
  }
  view.dispatch({
    changes: { from, to, insert: mark + sel + mark },
    selection: EditorSelection.range(from + mark.length, from + mark.length + sel.length),
    userEvent: "input",
  });
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
export function toggleListMarker(view: EditorView, kind: ListKind): void {
  const { state } = view;
  const range = state.selection.main;
  const start = state.doc.lineAt(range.from).from;
  const end = state.doc.lineAt(range.to).to;
  const lines = state.sliceDoc(start, end).split("\n");
  const allHaveKind = lines.every((line) => !line.trim() || lineHasMarkerKind(line, kind));
  const next = lines
    .map((line, i) => {
      if (!line.trim()) return line;
      const m = line.match(LIST_PREFIX_RE);
      const indent = m ? (m[1] ?? "") : ((line.match(/^\s*/) || [""])[0] ?? "");
      const rest = m ? line.slice(m[0].length) : line.slice(indent.length);
      if (allHaveKind) return indent + rest;
      if (kind === "task") return `${indent}- [ ] ${rest}`;
      if (kind === "ordered") return `${indent}${i + 1}. ${rest}`;
      return `${indent}- ${rest}`;
    })
    .join("\n");
  view.focus();
  view.dispatch({
    changes: { from: start, to: end, insert: next },
    selection: EditorSelection.range(start, start + next.length),
    userEvent: "input",
  });
}

/* Enter inside a list item: continue the list (numbered markers increment,
   task items get a fresh "[ ] "); Enter on an empty item removes the marker
   and ends the list. Falls through to the default newline otherwise. */
const continueList: Command = (view) => {
  const { state } = view;
  const range = state.selection.main;
  if (!range.empty) return false;
  const line = state.doc.lineAt(range.head);
  const before = state.sliceDoc(line.from, range.head);
  const m = before.match(LIST_PREFIX_RE);
  if (!m) return false;
  if (!before.slice(m[0].length)) {
    view.dispatch({
      changes: { from: line.from, to: range.head, insert: "" },
      userEvent: "delete",
    });
    return true;
  }
  let marker = m[2] ?? "";
  const num = marker.match(/^(\d+)([.)])$/);
  if (num) marker = `${Number(num[1]) + 1}${num[2]}`;
  const insert = "\n" + (m[1] ?? "") + marker + (m[3] ?? "") + (m[4] ? "[ ] " : "");
  view.dispatch({
    changes: { from: range.head, to: range.head, insert },
    selection: EditorSelection.cursor(range.head + insert.length),
    userEvent: "input",
  });
  return true;
};

/* Tab indents a list line (or inserts two spaces elsewhere); Shift+Tab
   outdents a list line by up to two leading spaces. */
const indentOrTab: Command = (view) => {
  const { state } = view;
  const range = state.selection.main;
  const line = state.doc.lineAt(range.head);
  if (LIST_PREFIX_RE.test(line.text)) {
    view.dispatch({
      changes: { from: line.from, to: line.from, insert: "  " },
      selection: EditorSelection.cursor(range.head + 2),
      userEvent: "input",
    });
  } else {
    view.dispatch({
      changes: { from: range.from, to: range.to, insert: "  " },
      selection: EditorSelection.cursor(range.from + 2),
      userEvent: "input",
    });
  }
  return true;
};

const outdentList: Command = (view) => {
  const { state } = view;
  const range = state.selection.main;
  const line = state.doc.lineAt(range.head);
  if (!LIST_PREFIX_RE.test(line.text)) return true; // swallow like the old editor
  const removed = Math.min(2, ((line.text.match(/^ */) || [""])[0] ?? "").length);
  if (removed === 0) return true;
  view.dispatch({
    changes: { from: line.from, to: line.from + removed, insert: "" },
    selection: EditorSelection.cursor(Math.max(line.from, range.head - removed)),
    userEvent: "delete",
  });
  return true;
};

/* High precedence: @uiw registers basicSetup's keymaps before user
   extensions, and its default Enter binding would otherwise win. */
export const markdownKeys = Prec.high(
  keymap.of([
    { key: "Mod-b", run: (view) => (toggleWrap(view, "**"), true) },
    { key: "Mod-i", run: (view) => (toggleWrap(view, "*"), true) },
    { key: "Enter", run: continueList },
    { key: "Tab", run: indentOrTab, shift: outdentList },
  ])
);
