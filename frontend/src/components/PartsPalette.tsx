import { useEffect, useState } from "react";
import { HTTP_URL } from "../config";
import { useStore } from "../lib/store";

interface CatalogPart {
  name: string;
  signature: string;
  doc: string;
  thumbnail: string;
}

export function PartsPalette() {
  const [parts, setParts] = useState<CatalogPart[]>([]);
  const [open, setOpen] = useState(true);
  const sendChat = useStore((s) => s.sendChat);

  useEffect(() => {
    fetch(`${HTTP_URL}/library`)
      .then((r) => r.json())
      .then((d: { parts: CatalogPart[] }) => setParts(d.parts ?? []))
      .catch(() => void 0);
  }, []);

  if (parts.length === 0) return null;

  return (
    <div className="palette">
      <button className="palette-header" onClick={() => setOpen((o) => !o)}>
        <span className={`palette-caret ${open ? "open" : ""}`}>▸</span>
        parts ({parts.length})
      </button>
      {open && (
        <div className="palette-grid">
          {parts.map((p) => (
            <div key={p.name} className="palette-card" title={p.doc}>
              <div className="palette-thumb" dangerouslySetInnerHTML={{ __html: p.thumbnail }} />
              <div className="palette-name">{p.name}</div>
              <button className="palette-add" onClick={() => sendChat(`Add a ${p.name} from the parts library to the model.`)}>
                + add
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
