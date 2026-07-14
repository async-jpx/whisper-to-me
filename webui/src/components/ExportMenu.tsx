import { useRef, useEffect, useState } from "react";
import { useStore } from "../store";
import { api } from "../api/client";
import type { ApiError } from "../api/client";
import type { ExportConfig } from "../api/types";
import { md, stripFrontmatter, noteTitleFromMd } from "../lib/markdown";
import { Icon } from "./Icons";
import { FollowupModal } from "./FollowupModal";

const EXPORT_CSS = `
    body { max-width: 46rem; margin: 2rem auto; padding: 0 1rem;
           font: 16px/1.6 -apple-system, "Segoe UI", sans-serif; color: #1f1f1f; }
    h1 { font-size: 1.5rem; } h2 { font-size: 1.15rem; margin-top: 1.6rem; }
    code { font-family: ui-monospace, Menlo, monospace; background: #f2f2f0;
           padding: 0.1em 0.3em; border-radius: 4px; }
    hr { border: 0; border-top: 1px solid #e4e4e2; margin: 1.5rem 0; }
    ul { padding-left: 1.4rem; } li { margin: 0.15rem 0; }
    input[type=checkbox] { margin-right: 0.4em; }
    blockquote { border-left: 3px solid #ddd; margin: 0; padding-left: 1rem; }`;

function slackText(mdText: string): string {
  let text = stripFrontmatter(mdText);
  const cut = text.search(/^## Transcript$/m);
  if (cut !== -1) text = text.slice(0, cut);
  return text
    .replace(/^#{1,6}\s+(.*)$/gm, "*$1*")
    .replace(/^\s*[-*+]\s+\[[xX]\]\s+/gm, "• ☑ ")
    .replace(/^\s*[-*+]\s+\[ \]\s+/gm, "• ☐ ")
    .replace(/^\s*[-*+]\s+/gm, "• ")
    .replace(/\*\*([^*]+)\*\*/g, "*$1*")
    .replace(/^---\s*$/gm, "")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function escapeHtml(text: string): string {
  const map: Record<string, string> = {
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  };
  return text.replace(/[&<>"']/g, (c) => map[c]!);
}

export function ExportMenu() {
  const menuRef = useRef<HTMLDivElement>(null);
  const [isOpen, setIsOpen] = useState(false);
  const [config, setConfig] = useState<ExportConfig | null>(null);
  const [draftData, setDraftData] = useState<string | null>(null);

  const currentNote = useStore((s) => s.currentNote);
  const currentNoteMd = useStore((s) => s.currentNoteMd);
  const toast = useStore((s) => s.toast);
  const confirmDialog = useStore((s) => s.confirmDialog);

  const handleOpenMenu = async (evt: React.MouseEvent) => {
    evt.stopPropagation();
    if (isOpen) {
      setIsOpen(false);
      return;
    }
    try {
      const cfg = await api.exportConfig();
      setConfig(cfg);
    } catch (err) {
      setConfig({ obsidian_vault: null, notion_configured: false });
    }
    setIsOpen(true);
  };

  const closeMenu = () => {
    setIsOpen(false);
  };

  const handleDocumentClick = (evt: MouseEvent) => {
    if (isOpen && menuRef.current && !menuRef.current.contains(evt.target as Node)) {
      closeMenu();
    }
  };

  const handleDocumentKeydown = (evt: KeyboardEvent) => {
    if (evt.key === "Escape" && isOpen) {
      closeMenu();
    }
  };

  useEffect(() => {
    if (!isOpen) return;
    document.addEventListener("click", handleDocumentClick);
    document.addEventListener("keydown", handleDocumentKeydown);
    return () => {
      document.removeEventListener("click", handleDocumentClick);
      document.removeEventListener("keydown", handleDocumentKeydown);
    };
  }, [isOpen]);

  const handleSlackCopy = async () => {
    closeMenu();
    try {
      await navigator.clipboard.writeText(slackText(currentNoteMd || ""));
      toast("Copied for Slack (summary only).");
    } catch (err) {
      toast("Could not copy to the clipboard.", "error");
    }
  };

  const handleDownloadHtml = () => {
    closeMenu();
    const body = md.render(stripFrontmatter(currentNoteMd || ""));
    const rawTitle: string = noteTitleFromMd(currentNoteMd || "") ?? currentNote ?? "note";
    const title = escapeHtml(rawTitle);
    const doc =
      `<!doctype html>\n<html lang="en"><head><meta charset="utf-8">` +
      `<title>${title}</title><style>${EXPORT_CSS}</style></head>\n` +
      `<body>${body}</body></html>\n`;
    const link = document.createElement("a");
    link.href = URL.createObjectURL(new Blob([doc], { type: "text/html" }));
    link.download = (currentNote || "note.md").replace(/\.md$/, ".html");
    link.click();
    URL.revokeObjectURL(link.href);
  };

  const handlePrint = () => {
    closeMenu();
    window.print();
  };

  const handleDraftFollowup = async () => {
    closeMenu();
    if (!currentNote) return;
    toast("Drafting a follow-up (local)…");
    try {
      const data = await api.followup(currentNote);
      setDraftData(data.draft || "");
    } catch (err) {
      const apiErr = err as ApiError;
      toast(apiErr.detail || "Could not draft a follow-up.", "error");
    }
  };

  const handleCopyToVault = async () => {
    closeMenu();
    try {
      await api.copyToVault(currentNote!);
      toast("Copied into the Obsidian vault.");
    } catch (err) {
      const apiErr = err as ApiError;
      toast(
        apiErr.status === 409 ? "That note is still being recorded." : "Could not copy to the vault.",
        "error"
      );
    }
  };

  const handlePushToNotion = async () => {
    closeMenu();
    const raw = useStore.getState().currentNoteMd || "";
    const titleStr: string = noteTitleFromMd(raw) ?? currentNote ?? "note";
    const ok = await confirmDialog(
      `Send this entire note — “${titleStr}” (title, date, attendees, summary and ` +
        `full transcript, exactly as shown) — to your Notion database via ` +
        `api.notion.com?\n\nThis is the only whisper-to-me action that sends ` +
        `anything off this machine. Nothing else is ever uploaded.`
    );
    if (!ok) return;
    toast("Pushing to Notion…");
    try {
      await api.pushToNotion(currentNote!);
      toast("Pushed to Notion.");
    } catch (err) {
      const apiErr = err as ApiError;
      toast(apiErr.detail || "Could not push to Notion.", "error");
    }
  };

  return (
    <>
      <span className="export-wrap" ref={menuRef}>
        <button className="btn btn-ghost btn-sm" onClick={handleOpenMenu}>
          <span>Export</span>
          <Icon name="chevronDown" />
        </button>
        {isOpen && (
          <div className="export-menu">
            <button className="export-item" onClick={handleSlackCopy}>
              Copy for Slack
            </button>
            <button className="export-item" onClick={handleDownloadHtml}>
              Download HTML
            </button>
            <button className="export-item" onClick={handlePrint}>
              Print / PDF…
            </button>
            <button className="export-item" onClick={handleDraftFollowup}>
              Draft follow-up email…
            </button>
            {config?.obsidian_vault && (
              <button className="export-item" onClick={handleCopyToVault}>
                Copy to Obsidian vault
              </button>
            )}
            {config?.notion_configured && (
              <button className="export-item" onClick={handlePushToNotion}>
                Push to Notion…
              </button>
            )}
          </div>
        )}
      </span>
      {draftData !== null && (
        <FollowupModal
          draft={draftData}
          onClose={() => setDraftData(null)}
        />
      )}
    </>
  );
}
