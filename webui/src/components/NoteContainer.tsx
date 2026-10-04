import { useEffect, useLayoutEffect, useRef, useState, useCallback } from "react";
import { useStore, type NoteTab } from "../store";
import { api } from "../api/client";
import type { ApiError } from "../api/client";
import { md, stripFrontmatter } from "../lib/markdown";
import { MarkdownEditor, type ReactCodeMirrorRef } from "./MarkdownEditor";
import { EditorToolbar } from "./EditorToolbar";
import { ExportMenu } from "./ExportMenu";
import { NoteHeader, RecordingMenu } from "./NoteHeader";
import { TranscriptTab } from "./TranscriptTab";

const TABS: [NoteTab, string][] = [
  ["summary", "Summary"],
  ["transcript", "Transcript"],
];

const STAMP_SPLIT_RE = /\[(\d+):(\d{2}):(\d{2})\]/;

/* The header and the Transcript tab already show the H1, the "Recorded …"
   line (when the header knows the date) and the transcript, so the summary
   hides them. Hidden, never removed: the nth task checkbox in DOM order must
   stay the nth task line in the file (see the change handler). */
function hideNonSummary(article: HTMLElement, headerHasDate: boolean): void {
  const first = article.firstElementChild;
  if (first?.tagName === "H1") {
    first.setAttribute("hidden", "");
    const next = first.nextElementSibling;
    if (
      headerHasDate &&
      next?.tagName === "P" &&
      next.children.length === 1 &&
      next.firstElementChild?.tagName === "EM" &&
      /^Record(ed|ing started) /.test(next.textContent ?? "")
    ) {
      next.setAttribute("hidden", "");
    }
  }
  for (const h2 of article.querySelectorAll(":scope > h2")) {
    if (h2.textContent?.trim() !== "Transcript") continue;
    const section = document.createElement("section");
    section.hidden = true;
    section.className = "md-transcript";
    const nodes: Node[] = [h2];
    for (let node = h2.nextSibling; node; node = node.nextSibling) {
      if (node instanceof Element && /^H[12]$/.test(node.tagName)) break;
      nodes.push(node);
    }
    const before = h2.previousElementSibling;
    if (before?.tagName === "HR") before.setAttribute("hidden", "");
    h2.replaceWith(section);
    section.append(...nodes);
    break;
  }
}

/* "[0:03:12]" in the summary becomes a link to that second of the
   transcript (and the recording, when one was kept). */
function linkStamps(article: HTMLElement, cue: (t: number) => void): void {
  article.querySelectorAll("p, li").forEach((node) => {
    if (node.closest(".md-transcript")) return;
    for (const child of [...node.childNodes]) {
      if (!(child instanceof Text)) continue;
      const parts = (child.textContent ?? "").split(STAMP_SPLIT_RE);
      if (parts.length < 5) continue;
      const frag = document.createDocumentFragment();
      for (let i = 0; i < parts.length; i += 4) {
        if (parts[i]) frag.append(parts[i] ?? "");
        const [h, m, sec] = [parts[i + 1], parts[i + 2], parts[i + 3]];
        if (h === undefined || m === undefined || sec === undefined) continue;
        const t = Number(h) * 3600 + Number(m) * 60 + Number(sec);
        const link = document.createElement("a");
        link.className = "stamp-link";
        link.href = "#";
        link.textContent = `[${h}:${m}:${sec}]`;
        link.addEventListener("click", (evt) => {
          evt.preventDefault();
          cue(t);
        });
        frag.append(link);
      }
      child.replaceWith(frag);
    }
  });
}

export function NoteContainer() {
  const ref = useRef<HTMLElement>(null);
  const cmRef = useRef<ReactCodeMirrorRef>(null);
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
  const noteTab = useStore((s) => s.noteTab);
  const setNoteTab = useStore((s) => s.setNoteTab);
  const meta = useStore((s) => s.notes.find((n) => n.name === s.currentNote));
  const pageRef = useRef<HTMLDivElement>(null);
  // The tabs share the one .content scroller; each keeps its own position
  // (saved on a tab click), and another note starts both at the top.
  const scrollTops = useRef<Record<NoteTab, number>>({ summary: 0, transcript: 0 });
  const scrollNoteRef = useRef(currentNote);

  useLayoutEffect(() => {
    if (scrollNoteRef.current !== currentNote) {
      scrollNoteRef.current = currentNote;
      scrollTops.current = { summary: 0, transcript: 0 };
    }
    const scroller = pageRef.current?.closest(".content");
    if (scroller) scroller.scrollTop = scrollTops.current[noteTab];
  }, [noteTab, currentNote]);

  const [previewHtml, setPreviewHtml] = useState("");
  const previewTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const schedulePreview = useCallback((text: string) => {
    if (previewTimerRef.current) clearTimeout(previewTimerRef.current);
    previewTimerRef.current = setTimeout(() => {
      setPreviewHtml(md.render(stripFrontmatter(text)));
    }, 150);
  }, []);

  // Compute the initial preview when entering edit mode (the editor focuses
  // itself via autoFocus).
  useEffect(() => {
    if (!editing) return;
    const text = useStore.getState().editorDraft ?? "";
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
  // toggle refetches it, and re-rendering then would reset scroll under the
  // user.
  useEffect(() => {
    if (!ref.current || editing) return;
    const { currentNoteMd, notes, cueTranscript } = useStore.getState();
    ref.current.innerHTML = md.render(stripFrontmatter(currentNoteMd || ""));

    const meta = notes.find((n) => n.name === currentNote);
    hideNonSummary(ref.current, !!meta?.date);
    linkStamps(ref.current, cueTranscript);

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
    if (!currentNote) return;
    const content =
      cmRef.current?.view?.state.doc.toString() ?? useStore.getState().editorDraft ?? "";
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

  const handleEditorChange = (text: string) => {
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
          <EditorToolbar target={cmRef} />
          <div className="editor-panes">
            <MarkdownEditor
              ref={cmRef}
              className="note-editor-cm"
              value={editorDraft ?? ""}
              onChange={handleEditorChange}
              autoFocus
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

  const selectTab = (tab: NoteTab) => {
    const scroller = pageRef.current?.closest(".content");
    if (scroller) scrollTops.current[noteTab] = scroller.scrollTop;
    setNoteTab(tab);
  };

  const onTabKey = (evt: React.KeyboardEvent) => {
    if (evt.key !== "ArrowLeft" && evt.key !== "ArrowRight") return;
    evt.preventDefault();
    const next: NoteTab = noteTab === "summary" ? "transcript" : "summary";
    selectTab(next);
    document.getElementById(`note-tab-${next}`)?.focus();
  };

  return (
    <div className="note-container" ref={pageRef}>
      <NoteHeader name={currentNote} meta={meta} />
      <div className="note-bar">
        <div className="tabs note-tabs" role="tablist" aria-label="Note" onKeyDown={onTabKey}>
          {TABS.map(([tab, label]) => (
            <button
              key={tab}
              id={`note-tab-${tab}`}
              role="tab"
              className={"tab" + (noteTab === tab ? " active" : "")}
              aria-selected={noteTab === tab}
              aria-controls={`note-panel-${tab}`}
              tabIndex={noteTab === tab ? 0 : -1}
              onClick={() => selectTab(tab)}
            >
              {label}
            </button>
          ))}
        </div>
        <span className="note-actions-bar">
          <button className="btn btn-ghost btn-sm" onClick={() => setEditing(true)}>
            Edit
          </button>
          <button className="btn btn-ghost btn-sm" onClick={handleCopy}>
            Copy
          </button>
          <ExportMenu />
          {currentNote && meta?.has_audio && <RecordingMenu name={currentNote} />}
        </span>
      </div>
      <div
        id="note-panel-summary"
        role="tabpanel"
        aria-labelledby="note-tab-summary"
        hidden={noteTab !== "summary"}
      >
        {/* Content is set imperatively by the effect above (innerHTML +
            post-processors), so React never diffs the processor-mutated DOM. */}
        <article ref={ref} className="note-view" />
      </div>
      <div
        id="note-panel-transcript"
        role="tabpanel"
        aria-labelledby="note-tab-transcript"
        hidden={noteTab !== "transcript"}
      >
        {currentNote && (
          <TranscriptTab key={currentNote} name={currentNote} hasAudio={!!meta?.has_audio} />
        )}
      </div>
    </div>
  );
}
