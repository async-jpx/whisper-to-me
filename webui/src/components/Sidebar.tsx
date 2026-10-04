import { useEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";
import { useStore, type View } from "../store";
import { api, ApiError } from "../api/client";
import { isActive } from "../api/types";
import type { NoteMeta, SearchResult } from "../api/types";
import { groupNotes, rowTime, type GroupMode } from "../lib/noteGroups";
import { Icon, type IconName } from "./Icons";
import { Logo } from "./Logo";
import { SearchResults, searchItems, type SearchItem } from "./SearchResults";

type NoteRef = Pick<NoteMeta, "name" | "title">;

const GROUP_KEY = "hush.sidebar.groupBy";

function readGroupMode(): GroupMode {
  try {
    return localStorage.getItem(GROUP_KEY) === "app" ? "app" : "day";
  } catch {
    return "day";
  }
}

function writeGroupMode(mode: GroupMode): void {
  try {
    localStorage.setItem(GROUP_KEY, mode);
  } catch {
    /* private window or blocked storage: the toggle still works this session */
  }
}

const IS_MAC = /Mac|iPhone|iPad/.test(navigator.platform);

function busyMessage(err: unknown, fallback: string): string {
  return err instanceof ApiError && err.status === 409
    ? "That note is still being recorded."
    : fallback;
}

function NavItem({
  icon,
  label,
  view,
  onClick,
}: {
  icon: IconName;
  label: string;
  view?: View;
  onClick: () => void;
}) {
  const active = useStore((s) => view !== undefined && s.view === view);
  return (
    <button className={"sb-item" + (active ? " active" : "")} onClick={onClick}>
      <Icon name={icon} />
      <span>{label}</span>
    </button>
  );
}

export function Sidebar() {
  const view = useStore((s) => s.view);
  const viewArchived = useStore((s) => s.viewArchived);
  const setSidebarTab = useStore((s) => s.setSidebarTab);
  const navigate = useStore((s) => s.navigate);
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
  const drawerOpen = useStore((s) => s.drawerOpen);
  const setDrawerOpen = useStore((s) => s.setDrawerOpen);

  const [searchInput, setSearchInput] = useState("");
  const [groupMode, setGroupMode] = useState<GroupMode>(readGroupMode);
  const searchTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const searchSeqRef = useRef(0);
  const searchRef = useRef<HTMLInputElement>(null);
  const [activeHit, setActiveHit] = useState(0);
  const items = useMemo(() => searchItems(searchResults ?? []), [searchResults]);

  useEffect(() => {
    return () => {
      if (searchTimerRef.current) clearTimeout(searchTimerRef.current);
    };
  }, []);

  useEffect(() => setActiveHit(0), [searchResults]);

  // ⌘K / Ctrl+K focuses search from anywhere, opening the drawer on narrow
  // windows and leaving the archive view (which unmounts the field). The
  // focus waits for the commit, when the field exists and the drawer is open.
  const [focusSearchSeq, setFocusSearchSeq] = useState(0);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!(e.metaKey || e.ctrlKey) || e.altKey || e.shiftKey || e.key.toLowerCase() !== "k") return;
      e.preventDefault();
      const s = useStore.getState();
      if (s.viewArchived) s.setSidebarTab(false);
      s.setDrawerOpen(true);
      setFocusSearchSeq((n) => n + 1);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);
  useEffect(() => {
    if (focusSearchSeq === 0) return;
    searchRef.current?.focus();
    searchRef.current?.select();
  }, [focusSearchSeq]);

  const groups = useMemo(() => groupNotes(notes, groupMode, new Date()), [notes, groupMode]);

  const chooseGroupMode = (mode: GroupMode) => {
    setGroupMode(mode);
    writeGroupMode(mode);
  };

  // Takes the query as a parameter: a debounced call reading `searchInput`
  // from its own render's closure would search one keystroke behind.
  const runSearch = async (value: string) => {
    const q = value.trim();
    const seq = ++searchSeqRef.current;
    if (!q) {
      setSearchResults(null);
      return;
    }
    let results: SearchResult[] = [];
    try {
      results = await api.search(q);
    } catch {
      /* daemon hiccup: show "No matches" rather than a stale list */
    }
    if (seq !== searchSeqRef.current) return;
    setSearchResults(results);
  };

  const handleSearchInput = (value: string) => {
    setSearchInput(value);
    if (searchTimerRef.current) clearTimeout(searchTimerRef.current);
    searchTimerRef.current = setTimeout(() => void runSearch(value), 200);
  };

  const clearSearch = () => {
    setSearchInput("");
    if (searchTimerRef.current) clearTimeout(searchTimerRef.current);
    searchSeqRef.current++;
    setSearchResults(null);
  };

  const handleArchiveNote = async (note: NoteRef) => {
    try {
      await api.archiveNote(note.name);
      forgetOpenNote(note.name);
      await refreshNotes();
      await refreshArchived();
      toast(`Archived “${note.title}”`);
    } catch (err) {
      toast(busyMessage(err, "Could not archive the note."), "error");
    }
  };

  const handleDeleteNote = async (note: NoteRef) => {
    const ok = await confirmDialog(
      `Delete “${note.title}”?\n\nThis permanently removes the note file.`,
      { danger: true },
    );
    if (!ok) return;
    try {
      await api.deleteNote(note.name);
      forgetOpenNote(note.name);
      await refreshNotes();
      toast(`Deleted “${note.title}”`);
    } catch (err) {
      toast(busyMessage(err, "Could not delete the note."), "error");
    }
  };

  const handleRestoreNote = async (note: NoteRef) => {
    try {
      await api.restoreNote(note.name);
      await refreshArchived();
      await refreshNotes();
      toast(`Restored “${note.title}”`);
    } catch {
      toast("Could not restore the note.", "error");
    }
  };

  const handleDeleteArchivedNote = async (note: NoteRef) => {
    const ok = await confirmDialog(`Delete “${note.title}” permanently?`, { danger: true });
    if (!ok) return;
    try {
      await api.deleteArchived(note.name);
      await refreshArchived();
      toast(`Deleted “${note.title}”`);
    } catch {
      toast("Could not delete the note.", "error");
    }
  };

  const openItem = ({ result, hit }: SearchItem) => {
    if (hit?.kind === "line" && hit.t !== null) {
      void openNote(result.name, { tab: "transcript", t: hit.t });
    } else {
      void openNote(result.name);
    }
  };

  const onSearchKey = (e: ReactKeyboardEvent<HTMLInputElement>) => {
    if (e.nativeEvent.isComposing) return;
    switch (e.key) {
      case "ArrowDown":
      case "ArrowUp": {
        if (items.length === 0) return;
        e.preventDefault();
        const step = e.key === "ArrowDown" ? 1 : -1;
        setActiveHit((i) => (i + step + items.length) % items.length);
        return;
      }
      case "Enter": {
        const item = items[activeHit];
        if (!item) return;
        e.preventDefault();
        openItem(item);
        return;
      }
      case "Escape":
        if (searchInput) {
          e.preventDefault();
          e.stopPropagation(); // the first Esc clears; the drawer closes on the next
          clearSearch();
        } else {
          e.currentTarget.blur();
        }
        return;
    }
  };

  const noteRow = (note: NoteRef, meta: string) => (
    <li key={note.name}>
      <div className={"note-row" + (currentNote === note.name ? " active" : "")}>
        <button className="note-item" onClick={() => void openNote(note.name)}>
          <span className="note-title">{note.title}</span>
        </button>
        <span className="note-meta">{meta}</span>
        <div className="note-actions">
          <button
            className="note-action"
            title="Archive"
            aria-label="Archive note"
            onClick={() => void handleArchiveNote(note)}
          >
            <Icon name="archive" />
          </button>
          <button
            className="note-action note-action-danger"
            title="Delete"
            aria-label="Delete note"
            onClick={() => void handleDeleteNote(note)}
          >
            <Icon name="trash" />
          </button>
        </div>
      </div>
    </li>
  );

  const renderArchived = () => (
    <>
      <div className="sb-section-head">
        <button className="sb-back" onClick={() => setSidebarTab(false)}>
          <Icon name="chevronLeft" />
          <span>Archived</span>
        </button>
      </div>
      {archived.length === 0 ? (
        <p className="notes-empty">Nothing archived.</p>
      ) : (
        <ul className="notes-list">
          {archived.map((note) => (
            <li key={note.name}>
              <div className="note-row">
                <span className="note-item note-item-static">
                  <span className="note-title">{note.title}</span>
                </span>
                <span className="note-meta">{rowTime(note, "app")}</span>
                <div className="note-actions">
                  <button
                    className="note-action"
                    title="Restore"
                    aria-label="Restore note"
                    onClick={() => void handleRestoreNote(note)}
                  >
                    <Icon name="restore" />
                  </button>
                  <button
                    className="note-action note-action-danger"
                    title="Delete permanently"
                    aria-label="Delete permanently"
                    onClick={() => void handleDeleteArchivedNote(note)}
                  >
                    <Icon name="trash" />
                  </button>
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}
    </>
  );

  const renderGroups = () => (
    <>
      <div className="sb-section-head">
        <span className="sb-group-label">Notes</span>
        <span className="sb-head-tools">
          <span className="segmented" role="radiogroup" aria-label="Group notes by">
            {(["day", "app"] as const).map((mode) => (
              <button
                key={mode}
                role="radio"
                aria-checked={groupMode === mode}
                className={groupMode === mode ? "active" : ""}
                onClick={() => chooseGroupMode(mode)}
              >
                {mode === "day" ? "Day" : "App"}
              </button>
            ))}
          </span>
          <button
            className="sb-icon-btn"
            title={archived.length ? `Archived (${archived.length})` : "Archived"}
            aria-label="Show archived notes"
            onClick={() => {
              clearSearch();
              setSidebarTab(true);
            }}
          >
            <Icon name="archive" />
          </button>
        </span>
      </div>
      {isActive(status) && (
        <button className={"live-item" + (view === "live" ? " active" : "")} onClick={openLive}>
          <span className={"status-dot status-" + status.state} />
          <span>Live session</span>
        </button>
      )}
      {notes.length === 0 && !isActive(status) && <p className="notes-empty">No notes yet.</p>}
      {groups.map((g) => (
        <section key={g.key} className="sb-group">
          <h3 className="sb-group-label">{g.label}</h3>
          <ul className="notes-list">{g.notes.map((n) => noteRow(n, rowTime(n, groupMode)))}</ul>
        </section>
      ))}
    </>
  );

  return (
    <>
      <aside className={"sidebar" + (drawerOpen ? " is-open" : "")} aria-label="Sidebar">
        <div className="sb-brand">
          <Logo size={26} />
          <span className="brand">Hush</span>
          <button
            className="sb-icon-btn sb-close"
            aria-label="Close sidebar"
            onClick={() => setDrawerOpen(false)}
          >
            <Icon name="close" />
          </button>
        </div>
        <nav className="sb-nav">
          <NavItem icon="plus" label="New meeting" view="home" onClick={() => void navigate("home")} />
          <NavItem icon="ask" label="Ask your notes" view="chat" onClick={() => void navigate("chat")} />
          <NavItem
            icon="templates"
            label="Templates"
            view="templates"
            onClick={() => void navigate("templates")}
          />
        </nav>
        {!viewArchived && (
          <label className="search-field">
            <Icon name="search" />
            <input
              ref={searchRef}
              type="search"
              placeholder="Search notes"
              aria-label="Search notes"
              role="combobox"
              aria-expanded={searchResults !== null}
              aria-controls="search-results"
              aria-autocomplete="list"
              aria-activedescendant={searchResults ? items[activeHit]?.id : undefined}
              autoComplete="off"
              spellCheck={false}
              value={searchInput}
              onChange={(e) => handleSearchInput(e.target.value)}
              onKeyDown={onSearchKey}
            />
            {!searchInput && <kbd className="search-kbd">{IS_MAC ? "⌘K" : "Ctrl K"}</kbd>}
          </label>
        )}
        <div className="sb-scroll">
          {viewArchived ? (
            renderArchived()
          ) : searchResults !== null ? (
            <SearchResults
              query={searchInput.trim()}
              results={searchResults}
              items={items}
              active={activeHit}
              onActivate={setActiveHit}
              onOpen={openItem}
            />
          ) : (
            renderGroups()
          )}
        </div>
        <div className="sb-footer">
          <button
            className={"sb-user" + (view === "settings" ? " active" : "")}
            onClick={() => void navigate("settings")}
          >
            <span className="sb-avatar">
              <Icon name="user" />
            </span>
            <span>Settings</span>
            <Icon name="settings" className="sb-user-gear" />
          </button>
        </div>
      </aside>
      <div
        className={"drawer-scrim" + (drawerOpen ? " is-open" : "")}
        onClick={() => setDrawerOpen(false)}
        aria-hidden="true"
      />
    </>
  );
}
