/* Templates: browse every template, star favorites, choose the default used
   when a meeting starts on "Auto", and create or delete your own. Built-ins
   are read-only (their sections can be viewed, never edited). Every change
   goes to the daemon first and the list is re-read from it, so the composer's
   picker and this page always agree with config.toml. */

import { useState } from "react";
import { useStore } from "../store";
import { api, ApiError } from "../api/client";
import type { Template } from "../api/types";
import {
  TEMPLATE_SKELETON,
  sectionTitles,
  sortTemplates,
  templateLabel,
  validateTemplate,
} from "../lib/templates";
import { Icon } from "./Icons";
import { MarkdownEditor } from "./MarkdownEditor";

function serverError(err: unknown, fallback: string): string {
  // 400/409 details are written for people; anything else gets the fallback.
  if (err instanceof ApiError) {
    return ((err.status === 400 || err.status === 409) && err.detail) || fallback;
  }
  return "Could not reach the daemon.";
}

function StarButton({
  template,
  disabled,
  onToggle,
}: {
  template: Template;
  disabled: boolean;
  onToggle: () => void;
}) {
  const label = template.favorite
    ? `Remove ${templateLabel(template)} from favorites`
    : `Add ${templateLabel(template)} to favorites`;
  return (
    <button
      className={"tpl-star" + (template.favorite ? " on" : "")}
      aria-pressed={template.favorite}
      aria-label={label}
      title={template.favorite ? "Favorite" : "Add to favorites"}
      disabled={disabled}
      onClick={onToggle}
    >
      <Icon name="star" />
    </button>
  );
}

function CreateForm({ onDone }: { onDone: () => void }) {
  const templates = useStore((s) => s.templates);
  const loadTemplates = useStore((s) => s.loadTemplates);
  const toast = useStore((s) => s.toast);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [body, setBody] = useState(TEMPLATE_SKELETON);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const submit = async () => {
    const draft = { name: name.trim(), description: description.trim(), body };
    const invalid = validateTemplate(draft, templates);
    if (invalid) {
      setError(invalid);
      return;
    }
    setSaving(true);
    try {
      const created = await api.createTemplate(draft);
      await loadTemplates();
      toast(`Created “${templateLabel(created)}”`);
      onDone();
    } catch (err) {
      setError(serverError(err, "Could not create the template."));
    } finally {
      setSaving(false);
    }
  };

  const edit = <T,>(set: (v: T) => void) => (v: T) => {
    set(v);
    setError(null);
  };

  return (
    <form
      className="settings-card tpl-form"
      onSubmit={(e) => {
        e.preventDefault();
        void submit();
      }}
    >
      <div className="connector-head">
        <h3>New template</h3>
      </div>
      <label className="field">
        <span>Name</span>
        <input
          className="input"
          type="text"
          placeholder="Design review"
          maxLength={80}
          autoFocus
          value={name}
          onChange={(e) => edit(setName)(e.target.value)}
        />
      </label>
      <label className="field">
        <span>Description</span>
        <input
          className="input"
          type="text"
          placeholder="What this meeting is for, in one line"
          maxLength={160}
          value={description}
          onChange={(e) => edit(setDescription)(e.target.value)}
        />
      </label>
      <div className="field">
        <span>Sections</span>
        <p className="tpl-form-hint">
          Each <code>## Heading</code> becomes a section of the summary, and the text under it tells
          the summarizer what to write there. Keep the <code>## Action Items</code> section with its{" "}
          <code>- [ ]</code> line: that is what makes tasks checkable.
        </p>
        <div className="tpl-editor">
          <MarkdownEditor value={body} onChange={edit(setBody)} className="tpl-editor-cm" />
        </div>
      </div>
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      <div className="connector-actions">
        <button type="button" className="btn btn-ghost" onClick={onDone} disabled={saving}>
          Cancel
        </button>
        <button type="submit" className="btn btn-primary" disabled={saving}>
          {saving ? "Creating…" : "Create template"}
        </button>
      </div>
    </form>
  );
}

function TemplateRow({
  template,
  isDefault,
  busy,
  expanded,
  onToggleExpanded,
  run,
}: {
  template: Template;
  isDefault: boolean;
  busy: boolean;
  expanded: boolean;
  onToggleExpanded: () => void;
  run: (action: () => Promise<unknown>, failure: string, success?: string) => void;
}) {
  const confirmDialog = useStore((s) => s.confirmDialog);
  const label = templateLabel(template);
  const sections = sectionTitles(template.body);
  const bodyId = `tpl-body-${template.name}`;

  const remove = async () => {
    const ok = await confirmDialog(
      `Delete the “${label}” template?\n\nMeetings already summarized with it keep their notes.`,
      { danger: true },
    );
    if (ok) run(() => api.deleteTemplate(template.name), "Could not delete the template.", `Deleted “${label}”`);
  };

  return (
    <li className={"tpl-row" + (expanded ? " is-open" : "")}>
      <div className="tpl-main">
        <StarButton
          template={template}
          disabled={busy}
          onToggle={() =>
            run(
              () => api.favoriteTemplate(template.name, !template.favorite),
              "Could not update favorites.",
            )
          }
        />
        <button
          className="tpl-summary"
          aria-expanded={expanded}
          aria-controls={bodyId}
          onClick={onToggleExpanded}
        >
          <span className="tpl-title-line">
            <span className="tpl-title">{label}</span>
            {isDefault && <span className="badge badge-accent">Default</span>}
            <span className="tpl-origin">{template.builtin ? "Built in" : "Yours"}</span>
          </span>
          {template.description && <span className="tpl-desc">{template.description}</span>}
          {sections.length > 0 && (
            <span className="tpl-sections" aria-label="Sections">
              {sections.map((s) => (
                <span key={s} className="tpl-section">
                  {s}
                </span>
              ))}
            </span>
          )}
        </button>
        <div className="tpl-actions">
          {isDefault ? (
            <button
              className="btn btn-ghost btn-sm"
              disabled={busy}
              onClick={() =>
                run(() => api.setDefaultTemplate(null), "Could not clear the default.", "No default template.")
              }
            >
              Clear default
            </button>
          ) : (
            <button
              className="btn btn-ghost btn-sm"
              disabled={busy}
              onClick={() =>
                run(
                  () => api.setDefaultTemplate(template.name),
                  "Could not set the default.",
                  `“${label}” is now the default.`,
                )
              }
            >
              Set as default
            </button>
          )}
          {!template.builtin && (
            <button
              className="note-action note-action-danger"
              aria-label={`Delete ${label}`}
              title="Delete"
              disabled={busy}
              onClick={() => void remove()}
            >
              <Icon name="trash" />
            </button>
          )}
          <button
            className="note-action tpl-chevron"
            aria-label={expanded ? "Hide sections" : "Show sections"}
            aria-expanded={expanded}
            aria-controls={bodyId}
            onClick={onToggleExpanded}
          >
            <Icon name="chevronDown" />
          </button>
        </div>
      </div>
      {expanded && (
        <div className="tpl-body" id={bodyId}>
          {template.builtin && <p className="tpl-readonly">Built-in templates are read-only.</p>}
          <pre>{template.body}</pre>
        </div>
      )}
    </li>
  );
}

export function TemplatesPage() {
  const templates = useStore((s) => s.templates);
  const defaultTemplate = useStore((s) => s.defaultTemplate);
  const loadTemplates = useStore((s) => s.loadTemplates);
  const toast = useStore((s) => s.toast);
  const [creating, setCreating] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const sorted = sortTemplates(templates);
  const defaultLabel = templates.find((t) => t.name === defaultTemplate);

  const runFor =
    (name: string) => (action: () => Promise<unknown>, failure: string, success?: string) => {
      setBusy(name);
      void (async () => {
        try {
          await action();
          if (success) toast(success);
        } catch (err) {
          toast(serverError(err, failure), "error");
        } finally {
          await loadTemplates();
          setBusy(null);
        }
      })();
    };

  return (
    <div className="page">
      <header className="page-head tpl-head">
        <div>
          <h1>Templates</h1>
          <p>
            A template decides the sections of a meeting’s summary. On <strong>Auto</strong>,{" "}
            {defaultLabel ? (
              <>
                Hush uses your default, <strong>{templateLabel(defaultLabel)}</strong>.
              </>
            ) : (
              <>
                Hush picks one that fits the meeting title, or uses <strong>General meeting</strong>.
                Set a default to always use the same one.
              </>
            )}
          </p>
        </div>
        {!creating && (
          <button className="btn btn-primary" onClick={() => setCreating(true)}>
            <Icon name="plus" />
            <span>New template</span>
          </button>
        )}
      </header>

      {creating && <CreateForm onDone={() => setCreating(false)} />}

      {sorted.length === 0 ? (
        <p className="page-loading">No templates found.</p>
      ) : (
        <ul className="card-list tpl-list">
          {sorted.map((t) => (
            <TemplateRow
              key={t.name}
              template={t}
              isDefault={t.name === defaultTemplate}
              busy={busy === t.name}
              expanded={expanded === t.name}
              onToggleExpanded={() => setExpanded((cur) => (cur === t.name ? null : t.name))}
              run={runFor(t.name)}
            />
          ))}
        </ul>
      )}
    </div>
  );
}
