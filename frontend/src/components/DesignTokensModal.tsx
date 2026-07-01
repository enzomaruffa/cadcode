import { useEffect, useState } from "react";
import { HTTP_URL } from "../config";
import { useStore } from "../lib/store";

interface Token {
  name: string;
  value: number;
  comment: string;
}

// View + edit the shared design tokens (lib/design.py). Shows what's importable
// and lets you tune values; saving rewrites the file and re-runs so every part
// re-derives.
export function DesignTokensModal({ onClose }: { onClose: () => void }) {
  const [tokens, setTokens] = useState<Token[]>([]);
  const [dirty, setDirty] = useState<Record<string, number>>({});
  const [saving, setSaving] = useState(false);
  const runNow = useStore((s) => s.runNow);

  useEffect(() => {
    fetch(`${HTTP_URL}/design`)
      .then((r) => r.json())
      .then((d: { tokens?: Token[] }) => setTokens(d.tokens ?? []))
      .catch(() => void 0);
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const valueOf = (t: Token) => (t.name in dirty ? dirty[t.name] : t.value);
  const hasEdits = Object.keys(dirty).length > 0;

  const save = async () => {
    if (!hasEdits) return onClose();
    setSaving(true);
    try {
      await fetch(`${HTTP_URL}/design`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ updates: dirty }),
      });
      runNow(); // recompute with the new tokens
      onClose();
    } catch {
      setSaving(false);
    }
  };

  const importLine = tokens.length ? `from lib.design import ${tokens.map((t) => t.name).join(", ")}` : "";

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal modal-help" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <span className="modal-title">design tokens</span>
          <button className="modal-close" onClick={onClose}>
            ×
          </button>
        </div>
        <div className="modal-body">
          <p className="help-intro">
            Shared constants every part imports from <code>lib.design</code>. Tune them here — your model and all
            library parts re-derive on the next run.
          </p>
          {importLine && <pre className="help-code">{importLine}</pre>}
          <div className="tokens-list">
            {tokens.map((t) => (
              <label className="token-row" key={t.name}>
                <span className="token-name">{t.name}</span>
                <span className="token-comment">{t.comment}</span>
                <input
                  className="token-input"
                  type="number"
                  step="0.1"
                  value={valueOf(t)}
                  onChange={(e) => setDirty((d) => ({ ...d, [t.name]: parseFloat(e.target.value) }))}
                />
              </label>
            ))}
            {tokens.length === 0 && <div className="lib-empty">loading…</div>}
          </div>
          <div className="colors-actions">
            <button className="btn btn-accept" onClick={save} disabled={saving}>
              {saving ? "saving…" : hasEdits ? "Save & re-run" : "Close"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
