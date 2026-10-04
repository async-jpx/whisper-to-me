import { Logo } from "./Logo";

export function DaemonDown() {
  return (
    <div className="daemon-down">
      <div className="daemon-down-card">
        <Logo size={48} />
        <h1>Hush is not running</h1>
        <p>This page can't reach the local daemon.</p>
        <p className="hint">
          Start it with <code>wtm ui</code> and this page picks it up on its own.
        </p>
        <p className="retrying">Retrying…</p>
      </div>
    </div>
  );
}
