import { useEffect, useState } from "react";
import { useStore } from "../store";

function ToastItem({ message, kind }: { message: string; kind: "info" | "error" }) {
  const [show, setShow] = useState(false);
  useEffect(() => {
    const raf = requestAnimationFrame(() => setShow(true));
    return () => cancelAnimationFrame(raf);
  }, []);
  return (
    <div
      className={
        "toast" + (kind === "error" ? " toast-error" : "") + (show ? " show" : "")
      }
    >
      {message}
    </div>
  );
}

export function Toasts() {
  const toasts = useStore((s) => s.toasts);
  return (
    <div className="toasts" aria-live="polite">
      {toasts.map((t) => (
        <ToastItem key={t.id} message={t.message} kind={t.kind} />
      ))}
    </div>
  );
}
