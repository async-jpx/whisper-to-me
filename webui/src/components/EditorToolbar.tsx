/* The formatting toolbar shared by the note editor and the live scratchpad.
   Buttons use onMouseDown preventDefault — a mousedown would blur the
   editor and lose its selection. */

import type { RefObject } from "react";
import type { ReactCodeMirrorRef } from "@uiw/react-codemirror";
import { toggleListMarker, toggleWrap, type ListKind } from "../lib/cm";

export function EditorToolbar({ target }: { target: RefObject<ReactCodeMirrorRef | null> }) {
  const run = (cmd: "bold" | "italic" | ListKind) => {
    const view = target.current?.view;
    if (!view) return;
    if (cmd === "bold") toggleWrap(view, "**");
    else if (cmd === "italic") toggleWrap(view, "*");
    else toggleListMarker(view, cmd);
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
