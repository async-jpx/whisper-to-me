import { useEffect, useRef, useState, useCallback } from "react";
import { useStore } from "../store";
import { api } from "../api/client";
import type { ApiError } from "../api/client";
import { md, stripFrontmatter } from "../lib/markdown";
import { markdownKeydown } from "../lib/editing";
import { EditorToolbar } from "./EditorToolbar";
import { ExportMenu } from "./ExportMenu";

const STAMP_RE = /^\[(\d+:\d{2}:\d{2})\]$/;

function foldTranscript(noteView: HTMLElement): void {
  for (const h2 of noteView.querySelectorAll("h2")) {
    if (h2.textContent?.trim() !== "Transcript") continue;
    const section: Node[] = [];
    for (let node = h2.nextSibling; node; node = node.nextSibling) {
      if (node.nodeType === 1 && /^H[12]$/.test((node as Element).tagName)) break;
      section.push(node);
    }
    const details = document.createElement("details");
    details.className = "transcript-fold";
    const summary = document.createElement("summary");
    summary.textContent = "Transcript";
    details.appendChild(summary);
    h2.replaceWith(details);
    section.forEach((node) => details.appendChild(node));
    break;
  }
}

function anchorStamps(noteView: HTMLElement): void {
  const fold = noteView.querySelector<HTMLDetailsElement>(".transcript-fold");
  if (!fold) return;
  const ids = new Map<string, string>(); // stamp text -> first anchor id
  fold.querySelectorAll("strong").forEach((strong) => {
    const text = strong.textContent || "";
    const m = text.match(STAMP_RE);
    if (!m || !m[1]) return;
    strong.classList.add("md-stamp");
    const stamp = m[1];
    if (!ids.has(stamp)) {
      strong.id = `t-${stamp.replace(/:/g, "-")}`;
      ids.set(stamp, strong.id);
    }
  });
  if (ids.size === 0) return;
  noteView.querySelectorAll("p, li").forEach((node) => {
    if (fold.contains(node)) return;
    for (const child of [...node.childNodes]) {
      if (child.nodeType !== 3) continue; // text nodes only
      const text = (child as Text).textContent || "";
      const parts = text.split(/\[(\d+:\d{2}:\d{2})\]/);
      if (parts.length < 3) continue;
      const frag = document.createDocumentFragment();
      parts.forEach((part, i) => {
        if (i % 2 === 0) {
          if (part) frag.appendChild(document.createTextNode(part));
          return;
        }
        const id = ids.get(part);
        if (!id) {
          frag.appendChild(document.createTextNode(`[${part}]`));
          return;
        }
        const link = document.createElement("a");
        link.className = "stamp-link";
        link.href = `#${id}`;
        link.textContent = `[${part}]`;
        link.addEventListener("click", (evt) => {
          evt.preventDefault();
          const target = document.getElementById(id);
          if (!target) return;
          const details = fold as HTMLDetailsElement;
          details.open = true;
          target.scrollIntoView({ behavior: "smooth", block: "center" });
          target.classList.remove("flash");
          requestAnimationFrame(() => target.classList.add("flash"));
        });
        frag.appendChild(link);
      });
      child.replaceWith(frag);
    }
  });
}

export function NoteContainer() {
  const ref = useRef<HTMLElement>(null);
  const taRef = useRef<HTMLTextAreaElement>(null);
  const currentNote = useStore((s) => s.currentNote);
  const noteRenderSeq = useStore((s) => s.noteRenderSeq);
  const editing = useStore((s) => s.editing);
  const setEditing = useStore((s) => s.setEditing);
  const editorDraft = useStore((s) => s.editorDraft);
  const setEditorDraft = useStore((s) => s.setEditorDraft);
  const editorDirty = useStore((s) => s.editorDirty);
  const noteSaved = useStore((s) => s.noteSaved);
  const confirmDialog = useStore((s) => s.confirmDialog);
  const taskToggled = useStore((s) => s.taskToggled);
  const toast = useStore((s) => s.toast);

  const [previewHtml, setPreviewHtml] = useState("");
  const previewTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const schedulePreview = useCallback((text: string) => {
    if (previewTimerRef.current) clearTimeout(previewTimerRef.current);
    previewTimerRef.current = setTimeout(() => {
      setPreviewHtml(md.render(stripFrontmatter(text)));
    }, 150);
  }, []);

  // Focus textarea and compute initial preview when entering edit mode
  useEffect(() => {
    if (!editing || !taRef.current) return;
    const ta = taRef.current;
    ta.focus();
    const text = ta.value;
    setPreviewHtml(md.render(stripFrontmatter(text)));
  }, [editing]);

  // Cleanup preview timer on unmount
  useEffect(() => {
    return () => {
      if (previewTimerRef.current) clearTimeout(previewTimerRef.current);
    };
  }, []);

  // Render + post-process imperatively, only on note open / save (like the
  // old renderNote). Deliberately NOT subscribed to currentNoteMd: a checkbox
  // toggle refetches it, and re-rendering then would collapse the transcript
  // fold and reset scroll under the user.
  useEffect(() => {
    if (!ref.current || editing) return;
    const raw = useStore.getState().currentNoteMd || "";
    ref.current.innerHTML = md.render(stripFrontmatter(raw));

    foldTranscript(ref.current);
    anchorStamps(ref.current);

    // Delegated checkbox listener: the nth input.task-list-item-checkbox in DOM
    // order toggles the nth task line in the file.
    const handleCheckboxChange = async (evt: Event) => {
      const target = evt.target as HTMLInputElement;
      if (!(target instanceof HTMLInputElement) || !target.classList.contains("task-list-item-checkbox")) {
        return;
      }

      const checkboxes = Array.from(
        ref.current!.querySelectorAll("input.task-list-item-checkbox")
      ) as HTMLInputElement[];
      const index = checkboxes.indexOf(target);

      try {
        await api.toggleTask(currentNote!, index, target.checked);
        await taskToggled();
      } catch (err) {
        target.checked = !target.checked;
        const apiErr = err as ApiError;
        toast(
          apiErr.status === 409
            ? "That note is still being recorded."
            : "Could not update the task.",
          "error"
        );
      }
    };

    const article = ref.current;
    article.addEventListener("change", handleCheckboxChange);
    return () => article.removeEventListener("change", handleCheckboxChange);
  }, [currentNote, noteRenderSeq, editing, taskToggled, toast]);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(useStore.getState().currentNoteMd || "");
      toast("Copied as Markdown.");
    } catch {
      toast("Could not copy to the clipboard.", "error");
    }
  };

  const handleSave = async () => {
    const ta = taRef.current;
    if (!ta || !currentNote) return;
    const content = ta.value;
    try {
      await api.putNote(currentNote, content);
      noteSaved(content);
      toast("Saved.");
    } catch (err) {
      const apiErr = err as ApiError;
      toast(
        apiErr.status === 409
          ? "That note is still being recorded."
          : "Could not save the note.",
        "error"
      );
    }
  };

  const handleCancel = async () => {
    if (editorDirty() && !(await confirmDialog("Discard your unsaved edits?"))) {
      return;
    }
    setEditing(false);
  };

  const handleEditorInput = () => {
    const ta = taRef.current;
    if (!ta) return;
    const text = ta.value;
    setEditorDraft(text);
    schedulePreview(text);
  };

  if (editing) {
    return (
      <div className="note-container editing">
        <div className="note-toolbar">
          <span id="edit-actions">
            <button className="btn btn-primary btn-sm" onClick={handleSave}>
              Save
            </button>
            <button className="btn btn-ghost btn-sm" onClick={handleCancel}>
              Cancel
            </button>
          </span>
        </div>
        <div className="editor-split">
          <EditorToolbar target={taRef} />
          <div className="editor-panes">
            <textarea
              ref={taRef}
              className="note-editor"
              spellCheck={false}
              defaultValue={editorDraft ?? ""}
              onKeyDown={markdownKeydown}
              onInput={handleEditorInput}
            />
            <article
              className="note-view editor-preview"
              aria-label="Preview"
              dangerouslySetInnerHTML={{ __html: previewHtml }}
            />
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="note-container">
      <div className="note-toolbar">
        <span id="view-actions">
          <button
            className="btn btn-ghost btn-sm"
            onClick={() => setEditing(true)}
          >
            Edit
          </button>
          <button
            className="btn btn-ghost btn-sm"
            onClick={handleCopy}
          >
            Copy
          </button>
          <ExportMenu />
        </span>
      </div>
      {/* Content is set imperatively by the effect above (innerHTML +
          post-processors), so React never diffs the processor-mutated DOM. */}
      <article ref={ref} className="note-view" />
    </div>
  );
}
