export function DaemonDown() {
  return (
    <div className="daemon-down">
      <div className="daemon-down-card">
        <h1>whisper-to-me</h1>
        <p>Can't reach the local daemon.</p>
        <p className="hint">
          Start it with <code>wtm ui</code>, then this page will pick up automatically.
        </p>
        <p className="retrying">Retrying&hellip;</p>
      </div>
    </div>
  );
}
