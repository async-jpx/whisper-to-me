import { useEffect, useRef, useState } from "react";
import { useStore } from "../store";
import { api } from "../api/client";
import type { Settings } from "../api/types";

interface SettingsModalProps {
  open: boolean;
  onClose: () => void;
}

export function SettingsModal({ open, onClose }: SettingsModalProps) {
  const toast = useStore((s) => s.toast);

  const [obsidianVault, setObsidianVault] = useState("");
  const [notionToken, setNotionToken] = useState("");
  const [notionDatabase, setNotionDatabase] = useState("");
  const [notionTokenSet, setNotionTokenSet] = useState(false);
  const [config, setConfig] = useState<Settings | null>(null);
  const modalRef = useRef<HTMLDivElement>(null);

  // Load settings when modal opens
  useEffect(() => {
    if (!open) return;

    const loadSettings = async () => {
      try {
        const cfg = await api.settings();
        setConfig(cfg);
        setObsidianVault(cfg.obsidian_vault || "");
        setNotionDatabase(cfg.notion_database_id || "");
        setNotionToken("");
        setNotionTokenSet(!!cfg.notion_token_set);
      } catch {
        toast("Could not load your connections.", "error");
      }
    };

    void loadSettings();
  }, [open, toast]);

  // Handle Escape key
  useEffect(() => {
    if (!open) return;

    const handleKeydown = (evt: KeyboardEvent) => {
      if (evt.key === "Escape") {
        onClose();
      }
    };

    document.addEventListener("keydown", handleKeydown);
    return () => document.removeEventListener("keydown", handleKeydown);
  }, [open, onClose]);

  // Handle backdrop click
  const handleBackdropClick = (evt: React.MouseEvent<HTMLDivElement>) => {
    if (evt.target === modalRef.current) {
      onClose();
    }
  };

  const applySettings = (cfg: Settings) => {
    setConfig(cfg);
    setObsidianVault(cfg.obsidian_vault || "");
    setNotionDatabase(cfg.notion_database_id || "");
    setNotionToken("");
    setNotionTokenSet(!!cfg.notion_token_set);
  };

  const saveConnector = async (
    method: string,
    path: string,
    body: unknown,
    okMsg: string
  ) => {
    try {
      const resp = await fetch(path, {
        method,
        headers: { "Content-Type": "application/json" },
        body: body !== null ? JSON.stringify(body) : undefined,
      });

      if (!resp.ok) {
        let detail = "Could not save your connection.";
        try {
          detail = (await resp.json()).detail || detail;
        } catch {
          /* keep the generic message */
        }
        toast(detail, "error");
        return;
      }

      const cfg: Settings = await resp.json();
      applySettings(cfg);
      toast(okMsg);
    } catch {
      toast("Could not reach the daemon.", "error");
    }
  };

  const handleObsidianConnect = () => {
    const vault = obsidianVault.trim();
    if (!vault) {
      toast("Enter a vault folder path.", "error");
      return;
    }
    void saveConnector("PUT", "/api/settings/obsidian", { vault }, "Obsidian connected.");
  };

  const handleObsidianDisconnect = () => {
    void saveConnector("DELETE", "/api/settings/obsidian", null, "Obsidian disconnected.");
  };

  const handleNotionConnect = () => {
    const token = notionToken.trim();
    const database_id = notionDatabase.trim();

    if (!database_id) {
      toast("Enter the Notion database ID.", "error");
      return;
    }

    if (!token && !notionTokenSet) {
      toast("Paste your Notion integration token.", "error");
      return;
    }

    const body = token ? { token, database_id } : { database_id };
    void saveConnector("PUT", "/api/settings/notion", body, "Notion connected.");
  };

  const handleNotionDisconnect = () => {
    void saveConnector("DELETE", "/api/settings/notion", null, "Notion disconnected.");
  };

  if (!open) return null;

  const obsidianConnected = !!config?.obsidian_vault;
  const notionConnected = !!config?.notion_configured;
  const notionTokenPlaceholder = config?.notion_token_set
    ? "•••••••• (saved — leave to keep)"
    : "ntn_…";

  return (
    <div
      ref={modalRef}
      className="modal"
      onClick={handleBackdropClick}
    >
      <div className="modal-card">
        <div className="modal-head">
          <span>Connections</span>
          <button
            className="modal-close"
            title="Close"
            onClick={onClose}
          >
            ✕
          </button>
        </div>

        <p className="settings-intro">
          Connect a destination to send your notes to. Everything else stays on this
          machine — only an explicit, per-note "Push to Notion" ever leaves it.
        </p>

        <div className={`connector${obsidianConnected ? " is-connected" : ""}`}>
          <div className="connector-head">
            <span className="connector-icon">🟣</span>
            <span className="connector-name">Obsidian</span>
            <span className="connector-status">
              {obsidianConnected ? "Connected" : "Not connected"}
            </span>
          </div>
          <p className="connector-desc">
            Copy notes into an Obsidian vault as local Markdown files — never leaves your machine.
          </p>
          <div className="connector-form">
            <label className="field">
              <span>Vault folder</span>
              <input
                type="text"
                spellCheck="false"
                placeholder="~/Vault/Meetings"
                value={obsidianVault}
                onChange={(e) => setObsidianVault(e.target.value)}
              />
            </label>
            <div className="connector-actions">
              {obsidianConnected && (
                <button
                  className="btn btn-ghost btn-sm"
                  onClick={handleObsidianDisconnect}
                >
                  Disconnect
                </button>
              )}
              <button
                className="btn btn-primary btn-sm"
                onClick={handleObsidianConnect}
              >
                {obsidianConnected ? "Save" : "Connect"}
              </button>
            </div>
          </div>
        </div>

        <div className={`connector${notionConnected ? " is-connected" : ""}`}>
          <div className="connector-head">
            <span className="connector-icon">⬛</span>
            <span className="connector-name">Notion</span>
            <span className="connector-status">
              {notionConnected ? "Connected" : "Not connected"}
            </span>
          </div>
          <p className="connector-desc">
            Push a note to a Notion database. Paste an internal integration token and the
            target database ID. Stored locally; nothing is sent until you push a note.
          </p>
          <div className="connector-form">
            <label className="field">
              <span>Integration token</span>
              <input
                type="password"
                spellCheck="false"
                autoComplete="off"
                placeholder={notionTokenPlaceholder}
                value={notionToken}
                onChange={(e) => setNotionToken(e.target.value)}
              />
            </label>
            <label className="field">
              <span>Database ID</span>
              <input
                type="text"
                spellCheck="false"
                autoComplete="off"
                placeholder="e.g. 24f1a2b3c4d5…"
                value={notionDatabase}
                onChange={(e) => setNotionDatabase(e.target.value)}
              />
            </label>
            <div className="connector-actions">
              <a
                className="connector-help"
                href="https://www.notion.so/my-integrations"
                target="_blank"
                rel="noreferrer noopener"
              >
                Create a token ↗
              </a>
              {(notionConnected || notionTokenSet) && (
                <button
                  className="btn btn-ghost btn-sm"
                  onClick={handleNotionDisconnect}
                >
                  Disconnect
                </button>
              )}
              <button
                className="btn btn-primary btn-sm"
                onClick={handleNotionConnect}
              >
                {notionConnected ? "Save" : "Connect"}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
