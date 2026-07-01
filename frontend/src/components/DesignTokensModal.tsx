import { useEffect, useRef, useState } from "react";
import { HTTP_URL } from "../config";
import { useStore } from "../lib/store";

interface Token {
  name: string;
  value: number;
  comment: string;
  min?: number;
  max?: number;
}

type Scope = "project" | "global";

// View + edit design tokens. When a project is active it edits THAT project's
// constants (project.py) — otherwise the global lib/design.py. Typed params
// (`Annotated[float, Range(a, b)]`) render as sliders and apply LIVE (the active
// scene re-derives as you drag); plain constants are number inputs.
export function DesignTokensModal({ onClose }: { onClose: () => void }) {
  const activeProject = useStore((s) => s.activeProject);
  const runNow = useStore((s) => s.runNow);
  const [scope, setScope] = useState<Scope>(activeProject ? "project" : "global");
  const [tokens, setTokens] = useState<Token[]>([]);
  const [dirty, setDirty] = useState<Record<string, number>>({});
  const [saving, setSaving] = useState(false);
  const [loading, setLoading] = useState(true);
  const applyTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const endpoint = scope === "project" && activeProject ? `/projects/${activeProject}/design` : `/design`;

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

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  useEffect(
    () => () => {
      if (applyTimer.current) clearTimeout(applyTimer.current);
    },
    [],
  );

  const switchScope = (s: Scope) => {
    if (s === scope) return;
    setScope(s);
    setDirty({});
    setLoading(true);
  };

  const valueOf = (t: Token) => (t.name in dirty ? dirty[t.name] : t.value);
  const hasEdits = Object.keys(dirty).length > 0;

  const applyLive = async (updates: Record<string, number>) => {
    try {
      await fetch(`${HTTP_URL}${endpoint}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ updates }),
      });
      runNow(); // re-derive the active scene/part with the new project constants
    } catch {
      /* ignore */
    }
  };

  // Update a value + apply live (debounced) so dragging re-derives the model.
  const change = (name: string, value: number) => {
    if (Number.isNaN(value)) return;
    const next = { ...dirty, [name]: value };
    setDirty(next);
    if (applyTimer.current) clearTimeout(applyTimer.current);
    applyTimer.current = setTimeout(() => void applyLive(next), 200);
  };

  const save = async () => {
    if (applyTimer.current) clearTimeout(applyTimer.current);
    if (hasEdits) {
      setSaving(true);
      await applyLive(dirty);
    }
    onClose();
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
                and scene. Give one a <code>Range(min, max)</code> for a slider; drag and the model re-derives live.
              </>
            ) : (
              <>
                Shared constants every part imports from <code>lib.design</code>. Tune them here — your model and all
                library parts re-derive live.
              </>
            )}
          </p>
          {importLine && <pre className="help-code">{importLine}</pre>}
          <div className="tokens-list">
            {tokens.map((t) => (
              <div className="token-row" key={t.name}>
                <span className="token-name">{t.name}</span>
                <span className="token-comment">{t.comment}</span>
                {t.min !== undefined && t.max !== undefined ? (
                  <div className="token-slider">
                    <input
                      className="param-slider"
                      type="range"
                      min={t.min}
                      max={t.max}
                      step={(t.max - t.min) / 100 || 0.1}
                      value={valueOf(t)}
                      onChange={(e) => change(t.name, parseFloat(e.target.value))}
                    />
                    <input
                      className="token-input"
                      type="number"
                      step="0.1"
                      value={valueOf(t)}
                      onChange={(e) => change(t.name, parseFloat(e.target.value))}
                    />
                  </div>
                ) : (
                  <input
                    className="token-input"
                    type="number"
                    step="0.1"
                    value={valueOf(t)}
                    onChange={(e) => change(t.name, parseFloat(e.target.value))}
                  />
                )}
              </div>
            ))}
            {loading && <div className="lib-empty">loading…</div>}
            {!loading && tokens.length === 0 && (
              <div className="lib-empty">
                {scope === "project"
                  ? "No numeric constants in project.py yet — add e.g. UNIT: Annotated[float, Range(5, 40)] = 10.0"
                  : "No tokens found."}
              </div>
            )}
          </div>
          <div className="colors-actions">
            <button className="btn btn-accept" onClick={save} disabled={saving}>
              {saving ? "saving…" : "Done"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
