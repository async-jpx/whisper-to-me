import { useRef, useEffect } from "react";
import { useStore } from "../store";
import { Icon } from "./Icons";

interface FollowupModalProps {
  draft: string;
  onClose: () => void;
}

export function FollowupModal({ draft, onClose }: FollowupModalProps) {
  const modalRef = useRef<HTMLDivElement>(null);
  const toast = useStore((s) => s.toast);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(draft);
      toast("Copied — nothing was sent anywhere.");
    } catch (err) {
      toast("Could not copy to the clipboard.", "error");
    }
  };

  const handleBackdropClick = (evt: React.MouseEvent) => {
    if (evt.target === modalRef.current) {
      onClose();
    }
  };

  const handleKeydown = (evt: KeyboardEvent) => {
    if (evt.key === "Escape") {
      onClose();
    }
  };

  useEffect(() => {
    document.addEventListener("keydown", handleKeydown);
    return () => document.removeEventListener("keydown", handleKeydown);
  }, []);

  return (
    <div className="modal" ref={modalRef} onClick={handleBackdropClick}>
      <div className="modal-card">
        <div className="modal-head">
          <span>Follow-up email draft</span>
          <button
            className="modal-close"
            title="Close"
            aria-label="Close"
            onClick={onClose}
          >
            <Icon name="close" />
          </button>
        </div>
        <textarea
          className="modal-text"
          readOnly
          spellCheck={false}
          value={draft}
        />
        <div className="modal-actions">
          <span className="modal-note">Local draft — nothing is sent anywhere.</span>
          <button className="btn btn-primary btn-sm" onClick={handleCopy}>
            Copy
          </button>
        </div>
      </div>
    </div>
  );
}
