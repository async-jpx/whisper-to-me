/* "New meeting": the start page. The composer's send button starts a
   recording; the typed text, if any, becomes the meeting title. */

import { useState } from "react";
import { useStore } from "../store";
import { canStartRecording, isActive } from "../api/types";
import { startRecording } from "../lib/record";
import { sortTemplates, templateLabel } from "../lib/templates";
import type { Template } from "../api/types";
import { Composer } from "./Composer";
import { Logo } from "./Logo";

export function HomeView() {
  const status = useStore((s) => s.status);
  const recordPending = useStore((s) => s.recordPending);
  const templates = useStore((s) => s.templates);
  const defaultTemplate = useStore((s) => s.defaultTemplate);
  const openLive = useStore((s) => s.openLive);
  const [title, setTitle] = useState("");
  const [template, setTemplate] = useState("");

  if (isActive(status)) {
    return (
      <div className="home">
        <Logo size={48} />
        <h1 className="home-title">A meeting is in progress</h1>
        <p className="home-sub">Finish it before starting another one.</p>
        <button className="btn btn-primary btn-lg" onClick={openLive}>
          Open the live session
        </button>
      </div>
    );
  }

  const configured = templates.find((t) => t.name === defaultTemplate);
  const autoHelp = configured
    ? `Auto uses your default, ${templateLabel(configured)}`
    : "Auto picks a template from the title";
  const sorted = sortTemplates(templates);
  const favorites = sorted.filter((t) => t.favorite);
  const others = sorted.filter((t) => !t.favorite);
  const option = (t: Template) => (
    <option key={t.name} value={t.name}>
      {templateLabel(t) + (t.name === defaultTemplate ? " · default" : "")}
    </option>
  );

  const start = (value: string) => {
    void startRecording(value.replace(/\s+/g, " ").trim() || null, template || null);
  };

  return (
    <div className="home">
      <Logo size={48} />
      <h1 className="home-title">What are we meeting about?</h1>
      <Composer
        className="home-composer"
        value={title}
        onChange={setTitle}
        onSubmit={start}
        placeholder="Meeting title (optional)"
        submitLabel="Start recording"
        submitIcon={<span className="rec-glyph" aria-hidden="true" />}
        allowEmpty
        maxLength={120}
        sendDisabled={recordPending || !canStartRecording(status)}
        autoFocus
        footer={
          <>
            <label className="pill-select" title={template ? undefined : autoHelp}>
              <span>Template</span>
              <select value={template} onChange={(e) => setTemplate(e.target.value)}>
                <option value="">{configured ? `Auto · ${templateLabel(configured)}` : "Auto"}</option>
                {favorites.length > 0 ? (
                  <>
                    <optgroup label="Favorites">{favorites.map(option)}</optgroup>
                    <optgroup label="All templates">{others.map(option)}</optgroup>
                  </>
                ) : (
                  others.map(option)
                )}
              </select>
            </label>
            <span className="composer-hint">
              {template ? "Press Enter to start recording" : `${autoHelp}. Enter starts recording.`}
            </span>
          </>
        }
      />
      <p className="home-privacy">Recorded, transcribed and summarized on this Mac. Nothing leaves it.</p>
    </div>
  );
}
