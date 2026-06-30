import { useState } from "react";
import { useStore } from "../lib/store";

export function HistoryControls() {
  const canUndo = useStore((s) => s.canUndo);
  const canRedo = useStore((s) => s.canRedo);
  const commits = useStore((s) => s.commits);
  const undo = useStore((s) => s.undo);
  const redo = useStore((s) => s.redo);
  const checkpoint = useStore((s) => s.checkpoint);
  const rollback = useStore((s) => s.rollback);

  const [open, setOpen] = useState(false);

  const doCheckpoint = () => {
    const msg = window.prompt("Checkpoint message:", "checkpoint");
    if (msg !== null) checkpoint(msg || "checkpoint");
  };

  return (
    <div className="history">
      <button className="hbtn" disabled={!canUndo} onClick={undo} title="Undo (buffer history)">
        ↶ undo
      </button>
      <button className="hbtn" disabled={!canRedo} onClick={redo} title="Redo">
        ↷ redo
      </button>
      <button className="hbtn" onClick={doCheckpoint} title="Commit a durable checkpoint (git)">
        ⎘ checkpoint
      </button>
      <button className="hbtn" onClick={() => setOpen((o) => !o)}>
        timeline ({commits.length})
      </button>

      {open && (
        <div className="timeline">
          {commits.length === 0 && <div className="timeline-empty">no checkpoints yet</div>}
          {commits.map((c) => (
            <button
              key={c.sha}
              className="timeline-row"
              onClick={() => {
                rollback(c.sha);
                setOpen(false);
              }}
              title={`roll back to ${c.short}`}
            >
              <code>{c.short}</code>
              <span className="timeline-msg">{c.message}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
