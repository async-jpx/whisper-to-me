/* Settings: General (recording retention) and Connections (Obsidian,
   Notion). Every save here is a write to the local config.toml through the
   daemon. Connecting Notion stores the token; it is not checked over the
   network until the first push. */

import { useEffect, useState } from "react";
import { useStore } from "../store";
import { api, ApiError } from "../api/client";
import type { Settings } from "../api/types";

type Section = "general" | "connections";

const SECTIONS: { id: Section; label: string }[] = [
  { id: "general", label: "General" },
  { id: "connections", label: "Connections" },
];

function errorText(err: unknown, fallback: string): string {
  // 400s carry a message written for people; anything else gets the fallback.
  if (err instanceof ApiError) return (err.status === 400 && err.detail) || fallback;
  return "Could not reach the daemon.";
}

function Toggle({
  checked,
  disabled,
  onChange,
  label,
}: {
  checked: boolean;
  disabled?: boolean;
  onChange: (checked: boolean) => void;
  label: string;
}) {
  return (
    <button
      role="switch"
      aria-checked={checked}
      aria-label={label}
      className={"switch" + (checked ? " on" : "")}
      disabled={disabled}
      onClick={() => onChange(!checked)}
    >
      <span className="switch-knob" />
    </button>
  );
}

function GeneralSection({ settings, apply }: { settings: Settings; apply: (s: Settings) => void }) {
  const toast = useStore((s) => s.toast);
  const [saving, setSaving] = useState(false);

  const setKeepAudio = async (keep: boolean) => {
    setSaving(true);
    try {
      apply(await api.setKeepAudio(keep));
      toast(keep ? "Recordings will be kept." : "Recordings will not be kept.");
    } catch (err) {
      toast(errorText(err, "Could not save the setting."), "error");
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="settings-card">
      <div className="setting-row">
        <div className="setting-text">
          <h3>Keep meeting recordings</h3>
          <p>
            Save one compressed recording of each meeting next to its note, so you can play it back
            from the transcript. Recordings stay on this Mac. A recording you have not played for 30
            days is deleted automatically; the note and transcript are always kept.
          </p>
        </div>
        <Toggle
          label="Keep meeting recordings"
          checked={settings.recording.keep_audio}
          disabled={saving}
          onChange={(v) => void setKeepAudio(v)}
        />
      </div>
    </section>
  );
}

function ConnectionsSection({
  settings,
  apply,
}: {
  settings: Settings;
  apply: (s: Settings) => void;
}) {
  const toast = useStore((s) => s.toast);
  const [vault, setVault] = useState(settings.obsidian_vault ?? "");
  const [token, setToken] = useState("");
  const [database, setDatabase] = useState(settings.notion_database_id ?? "");

  const save = async (call: () => Promise<Settings>, okMsg: string) => {
    try {
      const next = await call();
      apply(next);
      setVault(next.obsidian_vault ?? "");
      setDatabase(next.notion_database_id ?? "");
      setToken("");
      toast(okMsg);
    } catch (err) {
      toast(errorText(err, "Could not save your connection."), "error");
    }
  };

  const connectObsidian = () => {
    const v = vault.trim();
    if (!v) return toast("Enter a vault folder path.", "error");
    void save(() => api.connectObsidian(v), "Obsidian connected.");
  };

  const connectNotion = () => {
    const db = database.trim();
    const tok = token.trim();
    if (!db) return toast("Enter the Notion database ID.", "error");
    if (!tok && !settings.notion_token_set) return toast("Paste your Notion integration token.", "error");
    void save(() => api.connectNotion(db, tok || undefined), "Notion connected.");
  };

  const obsidianConnected = settings.obsidian_vault !== null;
  const notionConnected = settings.notion_configured;

  return (
    <>
      <p className="settings-intro">
        Send notes to a tool you already use. Everything else stays on this Mac. Only an explicit
        Push to Notion on a single note ever leaves it.
      </p>
      <section className="settings-card">
        <div className="connector-head">
          <h3>Obsidian</h3>
          <span className={"badge" + (obsidianConnected ? " badge-on" : "")}>
            {obsidianConnected ? "Connected" : "Not connected"}
          </span>
        </div>
        <p className="setting-desc">Copy notes into a vault as Markdown files. Local files only.</p>
        <label className="field">
          <span>Vault folder</span>
          <input
            className="input"
            type="text"
            spellCheck={false}
            placeholder="~/Vault/Meetings"
            value={vault}
            onChange={(e) => setVault(e.target.value)}
          />
        </label>
        <div className="connector-actions">
          {obsidianConnected && (
            <button
              className="btn btn-ghost"
              onClick={() => void save(api.disconnectObsidian, "Obsidian disconnected.")}
            >
              Disconnect
            </button>
          )}
          <button className="btn btn-primary" onClick={connectObsidian}>
            {obsidianConnected ? "Save" : "Connect"}
          </button>
        </div>
      </section>
      <section className="settings-card">
        <div className="connector-head">
          <h3>Notion</h3>
          <span className={"badge" + (notionConnected ? " badge-on" : "")}>
            {notionConnected ? "Connected" : "Not connected"}
          </span>
        </div>
        <p className="setting-desc">
          Push a note to a Notion database when you choose to. The token is stored on this Mac and
          nothing is sent until you push a note.
        </p>
        <label className="field">
          <span>Integration token</span>
          <input
            className="input"
            type="password"
            spellCheck={false}
            autoComplete="off"
            placeholder={settings.notion_token_set ? "Saved. Leave empty to keep it" : "ntn_…"}
            value={token}
            onChange={(e) => setToken(e.target.value)}
          />
        </label>
        <label className="field">
          <span>Database ID</span>
          <input
            className="input"
            type="text"
            spellCheck={false}
            autoComplete="off"
            placeholder="24f1a2b3c4d5…"
            value={database}
            onChange={(e) => setDatabase(e.target.value)}
          />
        </label>
        <div className="connector-actions">
          <a
            className="link"
            href="https://www.notion.so/my-integrations"
            target="_blank"
            rel="noreferrer noopener"
          >
            Create a token ↗
          </a>
          <span className="topbar-spacer" />
          {(notionConnected || settings.notion_token_set) && (
            <button
              className="btn btn-ghost"
              onClick={() => void save(api.disconnectNotion, "Notion disconnected.")}
            >
              Disconnect
            </button>
          )}
          <button className="btn btn-primary" onClick={connectNotion}>
            {notionConnected ? "Save" : "Connect"}
          </button>
        </div>
      </section>
    </>
  );
}

export function SettingsPage() {
  const toast = useStore((s) => s.toast);
  const [section, setSection] = useState<Section>("general");
  const [settings, setSettings] = useState<Settings | null>(null);

  useEffect(() => {
    let live = true;
    api
      .settings()
      .then((s) => live && setSettings(s))
      .catch(() => toast("Could not load your settings.", "error"));
    return () => {
      live = false;
    };
  }, [toast]);

  return (
    <div className="page">
      <header className="page-head">
        <h1>Settings</h1>
      </header>
      <div className="tabs" role="tablist">
        {SECTIONS.map((s) => (
          <button
            key={s.id}
            role="tab"
            aria-selected={section === s.id}
            className={"tab" + (section === s.id ? " active" : "")}
            onClick={() => setSection(s.id)}
          >
            {s.label}
          </button>
        ))}
      </div>
      {settings === null ? (
        <p className="page-loading">Loading…</p>
      ) : section === "general" ? (
        <GeneralSection settings={settings} apply={setSettings} />
      ) : (
        <ConnectionsSection settings={settings} apply={setSettings} />
      )}
    </div>
  );
}
