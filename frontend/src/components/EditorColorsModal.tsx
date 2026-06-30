import { useEffect, useState } from "react";
import {
  EDITOR_TOKENS,
  DEFAULT_EDITOR_COLORS,
  loadEditorColors,
  saveEditorColors,
  applyEditorTheme,
  type EditorColors,
} from "../lib/editorTheme";

// Live editor syntax-color picker. Changes apply to Monaco immediately and
// persist in localStorage; "reset" restores the warm defaults.
export function EditorColorsModal({ onClose }: { onClose: () => void }) {
  const [colors, setColors] = useState<EditorColors>(() => loadEditorColors());

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const apply = (next: EditorColors) => {
    setColors(next);
    applyEditorTheme(next);
    saveEditorColors(next);
  };

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal modal-sm" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <span className="modal-title">editor colors</span>
          <button className="modal-close" onClick={onClose}>
            ×
          </button>
        </div>
        <div className="modal-body">
          <div className="colors-list">
            {EDITOR_TOKENS.map((t) => (
              <label className="color-row" key={t.key}>
                <span className="color-label">{t.label}</span>
                <code className="color-hex">{colors[t.key]}</code>
                <input
                  type="color"
                  className="color-input"
                  value={colors[t.key]}
                  onChange={(e) => apply({ ...colors, [t.key]: e.target.value })}
                  aria-label={t.label}
                />
              </label>
            ))}
          </div>
          <div className="colors-actions">
            <button className="btn" onClick={() => apply({ ...DEFAULT_EDITOR_COLORS })}>
              Reset to defaults
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
