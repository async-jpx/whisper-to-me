/* The formatting toolbar shared by the note editor and the live scratchpad.
   Buttons use onMouseDown preventDefault — a mousedown would blur the
   textarea and lose its selection. */

import type { RefObject } from "react";
import { toggleListMarker, toggleWrap, type ListKind } from "../lib/editing";

export function EditorToolbar({ target }: { target: RefObject<HTMLTextAreaElement | null> }) {
  const run = (cmd: "bold" | "italic" | ListKind) => {
    const ta = target.current;
    if (!ta) return;
    if (cmd === "bold") toggleWrap(ta, "**");
    else if (cmd === "italic") toggleWrap(ta, "*");
    else toggleListMarker(ta, cmd);
  };
  const prevent = (evt: React.MouseEvent) => evt.preventDefault();

  return (
    <div className="editor-toolbar" role="toolbar" aria-label="Formatting">
      <button type="button" className="editor-tool" title="Bold (Cmd/Ctrl+B)" onMouseDown={prevent} onClick={() => run("bold")}>
        <strong>B</strong>
      </button>
      <button type="button" className="editor-tool" title="Italic (Cmd/Ctrl+I)" onMouseDown={prevent} onClick={() => run("italic")}>
        <em>I</em>
      </button>
      <span className="editor-tool-sep"></span>
      <button type="button" className="editor-tool" title="Bullet list" onMouseDown={prevent} onClick={() => run("bullet")}>
        &bull;
      </button>
      <button type="button" className="editor-tool" title="Numbered list" onMouseDown={prevent} onClick={() => run("ordered")}>
        1.
      </button>
      <button type="button" className="editor-tool" title="Checklist" onMouseDown={prevent} onClick={() => run("task")}>
        &#9745;
      </button>
    </div>
  );
}
