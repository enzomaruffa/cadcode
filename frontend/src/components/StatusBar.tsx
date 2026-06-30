import { useStore } from "../lib/store";

export function StatusBar() {
  const conn = useStore((s) => s.conn);
  const runState = useStore((s) => s.runState);
  const error = useStore((s) => s.error);
  const stale = useStore((s) => s.stale);

  const connDot =
    conn === "open" ? "ok" : conn === "connecting" ? "warn" : "err";
  const runLabel =
    runState === "running"
      ? "running…"
      : runState === "error"
        ? "error"
        : runState === "ok"
          ? "ok"
          : "idle";

  return (
    <div className="statusbar">
      <span className={`dot dot-${connDot}`} />
      <span className="status-conn">{conn}</span>
      <span className={`status-run status-run-${runState}`}>{runLabel}</span>
      {stale && <span className="status-stale">showing last good geometry</span>}
      {error && (
        <span className="status-error">
          {error.line ? `line ${error.line}: ` : ""}
          {error.message}
        </span>
      )}
    </div>
  );
}
