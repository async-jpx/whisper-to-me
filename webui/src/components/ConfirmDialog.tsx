/* In-app replacement for window.confirm(): the desktop shell's embedded
   webview doesn't render native JS confirm dialogs, so confirm() silently
   resolves falsy with no visible prompt. Never use window.confirm(). Driven
   by store.confirmDialog(message, {danger}) → Promise<boolean>. */

import { useEffect, useRef } from "react";
import { useStore } from "../store";

export function ConfirmDialog() {
  const confirm = useStore((s) => s.confirm);
  const resolveConfirm = useStore((s) => s.resolveConfirm);
  const okRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!confirm) return;
    okRef.current?.focus();
    const onKey = (evt: KeyboardEvent) => {
      if (evt.key === "Escape") resolveConfirm(false);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [confirm, resolveConfirm]);

  if (!confirm) return null;
  return (
    <div
      className="modal"
      onClick={(evt) => {
        if (evt.target === evt.currentTarget) resolveConfirm(false); // backdrop
      }}
    >
      <div className="modal-card">
        <p className="modal-message">{confirm.message}</p>
        <div className="modal-actions">
          <button className="btn btn-ghost btn-sm" onClick={() => resolveConfirm(false)}>
            Cancel
          </button>
          <button
            ref={okRef}
            className={"btn btn-primary btn-sm" + (confirm.danger ? " btn-danger" : "")}
            onClick={() => resolveConfirm(true)}
          >
            OK
          </button>
        </div>
      </div>
    </div>
  );
}
