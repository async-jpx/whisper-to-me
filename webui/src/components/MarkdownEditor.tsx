/* The shared markdown editor (note editor + live scratchpad): CodeMirror 6
   via @uiw/react-codemirror — everything bundled locally, nothing loads from
   the network at runtime. Colors come from app.css design tokens so the
   editor follows the app's light/dark scheme automatically. */

import { forwardRef } from "react";
import CodeMirror, { type ReactCodeMirrorRef } from "@uiw/react-codemirror";
import { markdown, markdownLanguage } from "@codemirror/lang-markdown";
import { HighlightStyle, syntaxHighlighting } from "@codemirror/language";
import { EditorView } from "@codemirror/view";
import { tags } from "@lezer/highlight";
import { markdownKeys } from "../lib/cm";

const theme = EditorView.theme({
  "&": {
    backgroundColor: "transparent",
    color: "var(--text)",
    fontSize: "inherit",
    height: "100%",
  },
  "&.cm-focused": { outline: "none" },
  ".cm-content": {
    fontFamily: "inherit",
    lineHeight: "1.6",
    caretColor: "var(--text)",
    padding: "0.6rem 0",
  },
  ".cm-line": { padding: "0 0.7rem" },
  ".cm-cursor, .cm-dropCursor": { borderLeftColor: "var(--text)" },
  "&.cm-focused > .cm-scroller > .cm-selectionLayer .cm-selectionBackground, .cm-selectionBackground, .cm-content ::selection":
    { backgroundColor: "var(--accent-tint)" },
  ".cm-placeholder": { color: "var(--text-secondary)" },
});

const mdHighlight = HighlightStyle.define([
  { tag: tags.heading, fontWeight: "700" },
  { tag: tags.strong, fontWeight: "700" },
  { tag: tags.emphasis, fontStyle: "italic" },
  { tag: tags.strikethrough, textDecoration: "line-through" },
  { tag: tags.link, color: "var(--accent)" },
  { tag: tags.url, color: "var(--accent)" },
  { tag: tags.monospace, fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace" },
  { tag: tags.quote, color: "var(--text-secondary)" },
  { tag: tags.processingInstruction, color: "var(--text-secondary)" }, // #, -, > markers
  { tag: tags.meta, color: "var(--text-secondary)" },
]);

const extensions = [
  // Our own keymap replaces addKeymap for exact parity with the old editor
  // (task-item continuation, two-space Tab indents).
  markdown({ base: markdownLanguage, addKeymap: false }),
  markdownKeys,
  syntaxHighlighting(mdHighlight),
  EditorView.lineWrapping,
  theme,
];

interface MarkdownEditorProps {
  value: string;
  onChange: (text: string) => void;
  placeholder?: string;
  autoFocus?: boolean;
  className?: string;
}

export const MarkdownEditor = forwardRef<ReactCodeMirrorRef, MarkdownEditorProps>(
  function MarkdownEditor({ value, onChange, placeholder, autoFocus, className }, ref) {
    return (
      <CodeMirror
        ref={ref}
        value={value}
        onChange={onChange}
        placeholder={placeholder}
        autoFocus={autoFocus}
        className={className}
        theme="none"
        extensions={extensions}
        basicSetup={{
          lineNumbers: false,
          foldGutter: false,
          highlightActiveLine: false,
          highlightActiveLineGutter: false,
          highlightSelectionMatches: false,
          allowMultipleSelections: false,
        }}
      />
    );
  }
);

export type { ReactCodeMirrorRef };
