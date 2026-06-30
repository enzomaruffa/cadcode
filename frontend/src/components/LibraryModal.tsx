import { useEffect, useState } from "react";
import { HTTP_URL } from "../config";
import { useStore } from "../lib/store";

interface CatalogPart {
  name: string;
  signature: string;
  doc: string;
  thumbnail: string;
  import: string;
  params?: { name: string; default: unknown }[];
}

export function LibraryModal({ onClose }: { onClose: () => void }) {
  const [parts, setParts] = useState<CatalogPart[]>([]);
  const [loading, setLoading] = useState(true);
  const sendChat = useStore((s) => s.sendChat);

  useEffect(() => {
    fetch(`${HTTP_URL}/library`)
      .then((r) => r.json())
      .then((d: { parts: CatalogPart[] }) => setParts(d.parts ?? []))
      .catch(() => void 0)
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <span className="modal-title">parts library</span>
          <button className="modal-close" onClick={onClose}>
            ×
          </button>
        </div>
        <div className="modal-body">
          {loading && <div className="lib-empty">loading…</div>}
          {!loading && parts.length === 0 && <div className="lib-empty">No parts in the library yet.</div>}
          <div className="lib-grid">
            {parts.map((p) => (
              <div key={p.name} className="lib-card">
                <div className="lib-thumb" dangerouslySetInnerHTML={{ __html: p.thumbnail }} />
                <div className="lib-meta">
                  <div className="lib-name">{p.name}</div>
                  <div className="lib-doc">{(p.doc || "").split("\n")[0]}</div>
                  <code className="lib-sig">{p.signature}</code>
                </div>
                <button
                  className="lib-add"
                  onClick={() => {
                    sendChat(`Add a ${p.name} from the parts library to the model.`);
                    onClose();
                  }}
                >
                  add to model
                </button>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
