import { useEffect, useMemo, useState } from "react";
import { HTTP_URL } from "../config";
import { useStore } from "../lib/store";
import { PartPreview } from "./PartPreview";

interface CatalogPart {
  name: string;
  signature: string;
  doc: string;
  thumbnail: string;
  import: string;
}

export function LibraryModal({ onClose }: { onClose: () => void }) {
  const [parts, setParts] = useState<CatalogPart[]>([]);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<CatalogPart | null>(null);
  const sendChat = useStore((s) => s.sendChat);

  useEffect(() => {
    fetch(`${HTTP_URL}/library`)
      .then((r) => r.json())
      .then((d: { parts: CatalogPart[] }) => setParts(d.parts ?? []))
      .catch(() => void 0)
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") (selected ? setSelected(null) : onClose());
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, selected]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return parts;
    return parts.filter((p) => (p.name + " " + p.doc).toLowerCase().includes(q));
  }, [parts, query]);

  const add = (p: CatalogPart) => {
    sendChat(`Add a ${p.name} from the parts library to the model.`);
    onClose();
  };

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          {selected ? (
            <button className="modal-back" onClick={() => setSelected(null)}>
              ‹ library
            </button>
          ) : (
            <span className="modal-title">parts library</span>
          )}
          {!selected && (
            <input
              className="lib-search"
              placeholder="search parts…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              autoFocus
            />
          )}
          <button className="modal-close" onClick={onClose}>
            ×
          </button>
        </div>

        <div className="modal-body">
          {selected ? (
            <div className="lib-detail">
              <PartPreview name={selected.name} />
              <div className="lib-detail-meta">
                <div className="lib-name">{selected.name}</div>
                <div className="lib-doc">{selected.doc}</div>
                <code className="lib-sig">{selected.signature}</code>
                <code className="lib-sig">{selected.import}</code>
                <div className="lib-detail-actions">
                  <button className="lib-add" onClick={() => add(selected)}>
                    add to model
                  </button>
                  <span className="lib-hint">drag in the preview to rotate</span>
                </div>
              </div>
            </div>
          ) : (
            <>
              {loading && <div className="lib-empty">loading…</div>}
              {!loading && filtered.length === 0 && <div className="lib-empty">No matching parts.</div>}
              <div className="lib-grid">
                {filtered.map((p) => (
                  <button key={p.name} className="lib-card" onClick={() => setSelected(p)} title="Click to preview">
                    <div className="lib-thumb" dangerouslySetInnerHTML={{ __html: p.thumbnail }} />
                    <div className="lib-meta">
                      <div className="lib-name">{p.name}</div>
                      <div className="lib-doc">{(p.doc || "").split("\n")[0]}</div>
                    </div>
                  </button>
                ))}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
