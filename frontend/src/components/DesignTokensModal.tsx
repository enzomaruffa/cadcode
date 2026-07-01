import { useEffect, useState } from "react";
import { HTTP_URL } from "../config";
import { useStore } from "../lib/store";

interface Token {
  name: string;
  value: number;
  comment: string;
}

type Scope = "project" | "global";

// View + edit design tokens. When a project is active it edits THAT project's
// constants (project.py) — first-class, project-scoped; otherwise the global
// shared tokens (lib/design.py). Saving rewrites the file and re-runs so every
// part/scene re-derives. A toggle lets you switch scope when a project is open.
export function DesignTokensModal({ onClose }: { onClose: () => void }) {
  const activeProject = useStore((s) => s.activeProject);
  const runNow = useStore((s) => s.runNow);
  const [scope, setScope] = useState<Scope>(activeProject ? "project" : "global");
  const [tokens, setTokens] = useState<Token[]>([]);
  const [dirty, setDirty] = useState<Record<string, number>>({});
  const [saving, setSaving] = useState(false);
  const [loading, setLoading] = useState(true);

  const endpoint = scope === "project" && activeProject ? `/projects/${activeProject}/design` : `/design`;

  // Fetch tokens whenever the scope (endpoint) changes. State is only set in the
  // async callbacks; scope switching resets dirty/loading in switchScope.
  useEffect(() => {
    let alive = true;
    fetch(`${HTTP_URL}${endpoint}`)
      .then((r) => r.json())
      .then((d: { tokens?: Token[] }) => alive && (setTokens(d.tokens ?? []), setLoading(false)))
      .catch(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [endpoint]);

  const switchScope = (s: Scope) => {
    if (s === scope) return;
    setScope(s);
    setDirty({});
    setLoading(true);
  };

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
      await fetch(`${HTTP_URL}${endpoint}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ updates: dirty }),
      });
      runNow(); // recompute with the new tokens (project runner if a project file is active)
      onClose();
    } catch {
      setSaving(false);
    }
  };

  const module = scope === "project" && activeProject ? "project" : "lib.design";
  const importLine = tokens.length ? `from ${module} import ${tokens.map((t) => t.name).join(", ")}` : "";

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal modal-help" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <span className="modal-title">
            {scope === "project" && activeProject ? `${activeProject} · constants` : "design tokens"}
          </span>
          <button className="modal-close" onClick={onClose}>
            ×
          </button>
        </div>
        <div className="modal-body">
          {activeProject && (
            <div className="scope-toggle">
              <button className={scope === "project" ? "on" : ""} onClick={() => switchScope("project")}>
                this project
              </button>
              <button className={scope === "global" ? "on" : ""} onClick={() => switchScope("global")}>
                global
              </button>
            </div>
          )}
          <p className="help-intro">
            {scope === "project" && activeProject ? (
              <>
                Constants for project <code>{activeProject}</code>, in <code>project.py</code> — imported by every part
                and scene. Tune them here; the project re-derives on the next run.
              </>
            ) : (
              <>
                Shared constants every part imports from <code>lib.design</code>. Tune them here — your model and all
                library parts re-derive on the next run.
              </>
            )}
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
            {loading && <div className="lib-empty">loading…</div>}
            {!loading && tokens.length === 0 && (
              <div className="lib-empty">
                {scope === "project"
                  ? "No numeric constants in project.py yet — add e.g. UNIT = 10.0 to it."
                  : "No tokens found."}
              </div>
            )}
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
