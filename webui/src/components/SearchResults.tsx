/* Sidebar search results, grouped per meeting: a header (title, day, app)
   and up to three hit rows. Snippets are note content, so they are rendered
   as React text only; the U+E000/U+E001 marker pairs become <mark>. The
   keyboard cursor lives in the Sidebar (it owns the input); this component
   only renders the flat item list it is given. */

import { useEffect, useRef, type ReactNode } from "react";
import type { SearchHit, SearchResult } from "../api/types";
import { dayLabel } from "../lib/noteGroups";

/* One keyboard stop. A meeting whose only hit is its title is a stop of its
   own (hit === null) so every result stays reachable with ↑/↓. */
export interface SearchItem {
  id: string;
  result: SearchResult;
  hit: SearchHit | null;
}

export function searchItems(results: readonly SearchResult[]): SearchItem[] {
  return results.flatMap((result, r): SearchItem[] => {
    const hits = result.hits.filter((h) => h.kind !== "title");
    if (hits.length === 0) return [{ id: `sr-${r}`, result, hit: null }];
    return hits.map((hit, h) => ({ id: `sr-${r}-${h}`, result, hit }));
  });
}

function Snippet({ text }: { text: string }) {
  const parts = text.split(/[]/);
  return (
    <>
      {parts.map((part, i) => (part ? i % 2 === 1 ? <mark key={i}>{part}</mark> : part : null))}
    </>
  );
}

export function clock(t: number): string {
  const s = Math.max(0, Math.floor(t));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const ss = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`;
}

function resultMeta(r: SearchResult, now: Date): string {
  const parts: string[] = [];
  if (r.date) {
    const d = new Date(r.date);
    if (!Number.isNaN(d.getTime())) parts.push(dayLabel(d, now));
  }
  if (r.app) parts.push(r.app);
  return parts.join(" · ");
}

function HitBody({ hit }: { hit: SearchHit }) {
  if (hit.kind === "line") {
    return (
      <>
        <span className="sr-hit-head">
          {hit.t !== null && <span className="sr-stamp">{clock(hit.t)}</span>}
          {hit.speaker && <span className="sr-speaker">{hit.speaker}</span>}
        </span>
        <span className="sr-snippet">
          <Snippet text={hit.snippet} />
        </span>
      </>
    );
  }
  return (
    <>
      <span className="sr-hit-head">
        <span className="sr-kind">Summary</span>
      </span>
      <span className="sr-snippet">
        <Snippet text={hit.snippet} />
      </span>
    </>
  );
}

export function SearchResults({
  query,
  results,
  items,
  active,
  onActivate,
  onOpen,
}: {
  query: string;
  results: readonly SearchResult[];
  items: readonly SearchItem[];
  active: number;
  onActivate: (index: number) => void;
  onOpen: (item: SearchItem) => void;
}) {
  const listRef = useRef<HTMLDivElement>(null);
  const activeId = items[active]?.id;

  useEffect(() => {
    if (!activeId) return;
    listRef.current?.querySelector(`#${activeId}`)?.scrollIntoView({ block: "nearest" });
  }, [activeId]);

  if (results.length === 0) {
    return (
      <div className="sr-empty">
        <p className="sr-empty-title">No notes mention “{query}”.</p>
        <p>Search looks through titles, summaries and transcripts. Try fewer or shorter words.</p>
      </div>
    );
  }

  const now = new Date();
  const indexOf = new Map(items.map((it, i) => [it.id, i]));

  const stop = (item: SearchItem, className: string, children: ReactNode) => {
    const i = indexOf.get(item.id) ?? -1;
    return (
      <button
        key={item.id}
        id={item.id}
        role="option"
        aria-selected={i === active}
        tabIndex={-1}
        className={className + (i === active ? " is-active" : "")}
        onMouseMove={() => i !== active && onActivate(i)}
        onMouseDown={(e) => e.preventDefault()} // keep focus in the search input
        onClick={() => onOpen(item)}
      >
        {children}
      </button>
    );
  };

  return (
    <div className="sr" ref={listRef}>
      <div className="sb-section-head">
        <span className="sb-group-label">
          {results.length === 1 ? "1 meeting" : `${results.length} meetings`}
        </span>
      </div>
      <div role="listbox" id="search-results" aria-label="Search results">
        {results.map((r, ri) => {
          const titleHit = r.hits.find((h) => h.kind === "title");
          const own = items.filter((it) => it.result === r);
          const headerIsStop = own.length === 1 && own[0]?.hit === null;
          const meta = resultMeta(r, now);
          const header = (
            <>
              <span className="sr-title">
                <Snippet text={titleHit?.snippet ?? r.title} />
              </span>
              {meta && <span className="sr-meta">{meta}</span>}
            </>
          );
          return (
            <section key={r.name} className="sr-group" role="group" aria-label={r.title}>
              {headerIsStop && own[0] ? (
                stop(own[0], "sr-head", header)
              ) : (
                <button
                  className="sr-head"
                  tabIndex={-1}
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => onOpen({ id: `sr-${ri}`, result: r, hit: null })}
                >
                  {header}
                </button>
              )}
              {!headerIsStop &&
                own.map((it) => it.hit && stop(it, "sr-hit sr-hit-" + it.hit.kind, <HitBody hit={it.hit} />))}
            </section>
          );
        })}
      </div>
    </div>
  );
}
