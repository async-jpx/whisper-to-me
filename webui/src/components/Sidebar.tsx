import { useEffect, useRef, useState } from "react";
import { useStore } from "../store";
import { api } from "../api/client";
import type { NoteMeta, SearchHit } from "../api/types";
import { Icon } from "./Icons";
import { SettingsModal } from "./SettingsModal";

function formatRelativeDate(iso: string): string {
  const then = new Date(iso);
  if (Number.isNaN(then.getTime())) return "";
  const diffMs = Date.now() - then.getTime();
  const diffDay = Math.floor(diffMs / 86400000);
  if (diffDay <= 0) {
    return then.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  }
  if (diffDay === 1) return "yesterday";
  if (diffDay < 7) return `${diffDay} days ago`;
  return then.toLocaleDateString([], { month: "short", day: "numeric" });
}

interface SnippetPart {
  text: string;
  marked: boolean;
}

function parseSnippet(snippet: string): SnippetPart[] {
  const parts: SnippetPart[] = [];
  const splits = snippet.split(/[]/);
  let marked = false;
  for (const part of splits) {
    if (part) {
      parts.push({ text: part, marked });
    }
    marked = !marked;
  }
  return parts;
}

export function Sidebar() {
  const viewArchived = useStore((s) => s.viewArchived);
  const setSidebarTab = useStore((s) => s.setSidebarTab);
  const openChat = useStore((s) => s.openChat);
  const notes = useStore((s) => s.notes);
  const archived = useStore((s) => s.archived);
  const searchResults = useStore((s) => s.searchResults);
  const setSearchResults = useStore((s) => s.setSearchResults);
  const currentNote = useStore((s) => s.currentNote);
  const openNote = useStore((s) => s.openNote);
  const openLive = useStore((s) => s.openLive);
  const forgetOpenNote = useStore((s) => s.forgetOpenNote);
  const refreshNotes = useStore((s) => s.refreshNotes);
  const refreshArchived = useStore((s) => s.refreshArchived);
  const status = useStore((s) => s.status);
  const confirmDialog = useStore((s) => s.confirmDialog);
  const toast = useStore((s) => s.toast);

  const [searchInput, setSearchInput] = useState("");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const searchTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const searchSeqRef = useRef(0);

  // Cleanup search timer on unmount
  useEffect(() => {
    return () => {
      if (searchTimerRef.current) clearTimeout(searchTimerRef.current);
    };
  }, []);

  // Takes the query as a parameter: a debounced call reading `searchInput`
  // from its own render's closure would search one keystroke behind.
  const runSearch = async (value: string) => {
    const q = value.trim();
    const seq = ++searchSeqRef.current;
    if (!q) {
      setSearchResults(null);
      return;
    }
    let results: SearchHit[] = [];
    try {
      results = await api.search(q);
    } catch {
      /* daemon hiccup: show "No matches." rather than a stale list */
      results = [];
    }
    if (seq !== searchSeqRef.current) return;
    setSearchResults(results);
  };

  const handleSearchInput = (value: string) => {
    setSearchInput(value);
    if (searchTimerRef.current) clearTimeout(searchTimerRef.current);
    searchTimerRef.current = setTimeout(() => void runSearch(value), 200);
  };

  const handleSearchKeydown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Escape") {
      setSearchInput("");
      if (searchTimerRef.current) clearTimeout(searchTimerRef.current);
      searchSeqRef.current++;
      setSearchResults(null);
    }
  };

  const handleArchiveNote = async (note: NoteMeta) => {
    try {
      await api.archiveNote(note.name);
      forgetOpenNote(note.name);
      await refreshNotes();
      await refreshArchived();
      toast(`Archived “${note.title}”`);
    } catch (err) {
      const isError = err instanceof Error;
      const status = isError && "status" in err ? (err as any).status : null;
      toast(
        status === 409 ? "That note is still being recorded." : "Could not archive the note.",
        "error"
      );
    }
  };

  const handleDeleteNote = async (note: NoteMeta) => {
    const ok = await confirmDialog(
      `Delete “${note.title}”?\n\nThis permanently removes the note file.`,
      { danger: true }
    );
    if (!ok) return;
    try {
      await api.deleteNote(note.name);
      forgetOpenNote(note.name);
      await refreshNotes();
      toast(`Deleted “${note.title}”`);
    } catch (err) {
      const isError = err instanceof Error;
      const status = isError && "status" in err ? (err as any).status : null;
      toast(
        status === 409 ? "That note is still being recorded." : "Could not delete the note.",
        "error"
      );
    }
  };

  const handleRestoreNote = async (note: NoteMeta) => {
    try {
      await api.restoreNote(note.name);
      await refreshArchived();
      await refreshNotes();
      toast(`Restored “${note.title}”`);
    } catch {
      toast("Could not restore the note.", "error");
    }
  };

  const handleDeleteArchivedNote = async (note: NoteMeta) => {
    const ok = await confirmDialog(`Delete “${note.title}” permanently?`, {
      danger: true,
    });
    if (!ok) return;
    try {
      await api.deleteArchived(note.name);
      await refreshArchived();
      toast(`Deleted “${note.title}”`);
    } catch {
      toast("Could not delete the note.", "error");
    }
  };

  const handleNotesTabClick = () => {
    setSidebarTab(false);
    setSearchInput("");
    setSearchResults(null);
  };

  const renderArchivedList = () => {
    if (archived.length === 0) {
      return <div className="notes-empty">No archived notes.</div>;
    }
    return archived.map((note) => (
      <li key={note.name}>
        <div className="note-row">
          <div className="note-item note-item-static">
            <span className="note-title">{note.title}</span>
            <span className="note-date">{formatRelativeDate(note.modified)}</span>
          </div>
          <div className="note-actions">
            <button
              className="note-action"
              title="Restore note"
              aria-label="Restore note"
              onClick={(e) => {
                e.stopPropagation();
                void handleRestoreNote(note);
              }}
            >
              <Icon name="restore" />
            </button>
            <button
              className="note-action note-action-danger"
              title="Delete permanently"
              aria-label="Delete permanently"
              onClick={(e) => {
                e.stopPropagation();
                void handleDeleteArchivedNote(note);
              }}
            >
              <Icon name="trash" />
            </button>
          </div>
        </div>
      </li>
    ));
  };

  const renderNotesList = () => {
    if (viewArchived) {
      return renderArchivedList();
    }

    const entries: (NoteMeta | null)[] = [];

    // Add live session pseudo-entry if not idle
    if (status.state !== "idle") {
      entries.push(null); // placeholder for live item
    }

    // Add search results or notes
    const searching = searchResults !== null;
    const notesList = searching ? searchResults : notes;
    entries.push(...notesList);

    // Render empty state if needed
    if (notesList.length === 0) {
      if (searching || status.state === "idle") {
        return <div className="notes-empty">{searching ? "No matches." : "No notes yet."}</div>;
      }
      return null;
    }

    // Render all entries
    return entries.map((entry) => {
      // Live session item
      if (entry === null) {
        return (
          <li key="live-session">
            <button
              className={"live-item" + (currentNote === null ? " active" : "")}
              onClick={() => openLive()}
            >
              <span
                className={"status-dot status-" + status.state}
                style={{ width: 7, height: 7 }}
              />
              <span>Live session</span>
            </button>
          </li>
        );
      }

      // Regular note item
      const note = entry as NoteMeta;
      const searching = searchResults !== null;
      const hit = (searching ? (searchResults?.find((r) => r.name === note.name) as SearchHit | undefined) : undefined);

      return (
        <li key={note.name}>
          <div className="note-row">
            <button
              className={"note-item" + (currentNote === note.name ? " active" : "")}
              onClick={() => void openNote(note.name)}
            >
              <span className="note-title">{note.title}</span>
              <span className="note-date">{formatRelativeDate(note.modified)}</span>
              {searching && hit?.snippet && (
                <span className="note-snippet">
                  {parseSnippet(hit.snippet).map((part, i) =>
                    part.marked ? (
                      <mark key={i}>{part.text}</mark>
                    ) : (
                      <span key={i}>{part.text}</span>
                    )
                  )}
                </span>
              )}
            </button>
            <div className="note-actions">
              <button
                className="note-action"
                title="Archive note"
                aria-label="Archive note"
                onClick={(e) => {
                  e.stopPropagation();
                  void handleArchiveNote(note);
                }}
              >
                <Icon name="archive" />
              </button>
              <button
                className="note-action note-action-danger"
                title="Delete note"
                aria-label="Delete note"
                onClick={(e) => {
                  e.stopPropagation();
                  void handleDeleteNote(note);
                }}
              >
                <Icon name="trash" />
              </button>
            </div>
          </div>
        </li>
      );
    });
  };

  return (
    <aside className="sidebar">
      <div className="sidebar-header">
        <span className="brand">whisper-to-me</span>
        <span className="sidebar-header-actions">
          <button className="btn btn-ghost btn-sm" onClick={() => openChat()}>
            <Icon name="ask" />
            <span>Ask</span>
          </button>
          <button
            className="btn btn-ghost btn-sm"
            title="Connections & settings"
            aria-label="Settings"
            onClick={() => setSettingsOpen(true)}
          >
            <Icon name="settings" />
          </button>
        </span>
      </div>
      {!viewArchived && (
        <div className="sidebar-search">
          <input
            id="search-input"
            type="search"
            placeholder="Search notes…"
            autoComplete="off"
            value={searchInput}
            onChange={(e) => handleSearchInput(e.target.value)}
            onKeyDown={handleSearchKeydown}
          />
        </div>
      )}
      <div className="sidebar-tabs">
        <button
          className={"sidebar-tab" + (viewArchived ? "" : " active")}
          onClick={handleNotesTabClick}
        >
          Notes
        </button>
        <button
          className={"sidebar-tab" + (viewArchived ? " active" : "")}
          onClick={() => setSidebarTab(true)}
        >
          {archived.length ? `Archived (${archived.length})` : "Archived"}
        </button>
      </div>
      <ul className="notes-list">{renderNotesList()}</ul>
      <SettingsModal open={settingsOpen} onClose={() => setSettingsOpen(false)} />
    </aside>
  );
}
