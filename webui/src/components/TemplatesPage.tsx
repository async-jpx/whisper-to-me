/* Templates page placeholder: lists what the daemon offers. Creating,
   favoriting and the default template land in a later change. */

import { useStore } from "../store";

export function TemplatesPage() {
  const templates = useStore((s) => s.templates);
  return (
    <div className="page">
      <header className="page-head">
        <h1>Templates</h1>
        <p>Templates shape the sections of your summary. Pick one when you start a meeting.</p>
      </header>
      <ul className="card-list">
        {templates.map((t) => (
          <li key={t.name} className="card-row">
            <span className="card-row-title">{t.title || t.name}</span>
            <span className="card-row-meta">{t.builtin ? "Built in" : "Yours"}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
