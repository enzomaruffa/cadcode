import { useState } from "react";
import { useStore } from "../lib/store";

export function TabBar() {
  const docs = useStore((s) => s.docs);
  const activeId = useStore((s) => s.activeDocId);
  const switchDoc = useStore((s) => s.switchDoc);
  const closeDoc = useStore((s) => s.closeDoc);
  const newDoc = useStore((s) => s.newDoc);
  const renameDoc = useStore((s) => s.renameDoc);
  const [editing, setEditing] = useState<string | null>(null);

  return (
    <div className="tabbar">
      {docs.map((d) => (
        <div key={d.id} className={`tab ${d.id === activeId ? "tab-active" : ""}`} onClick={() => switchDoc(d.id)}>
          {editing === d.id ? (
            <input
              className="tab-rename"
              defaultValue={d.name}
              autoFocus
              onClick={(e) => e.stopPropagation()}
              onBlur={(e) => {
                renameDoc(d.id, e.target.value.trim());
                setEditing(null);
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter") (e.target as HTMLInputElement).blur();
                if (e.key === "Escape") setEditing(null);
              }}
            />
          ) : (
            <span
              className="tab-name"
              onDoubleClick={(e) => {
                e.stopPropagation();
                setEditing(d.id);
              }}
            >
              {d.name}
            </span>
          )}
          {docs.length > 1 && (
            <button
              className="tab-close"
              title="Close"
              onClick={(e) => {
                e.stopPropagation();
                closeDoc(d.id);
              }}
            >
              ×
            </button>
          )}
        </div>
      ))}
      <button className="tab-new" title="New part" onClick={newDoc}>
        +
      </button>
    </div>
  );
}
