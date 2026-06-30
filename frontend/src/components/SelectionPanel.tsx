import { useState } from "react";
import { useStore } from "../lib/store";

function fmt(v: unknown): string {
  if (Array.isArray(v)) return `(${v.map((x) => (typeof x === "number" ? x.toFixed(2) : String(x))).join(", ")})`;
  if (typeof v === "number") return v.toFixed(3);
  return String(v);
}

export function SelectionPanel() {
  const selection = useStore((s) => s.selection);
  const clear = useStore((s) => s.clearSelection);
  const sendChat = useStore((s) => s.sendChat);
  const [extrude, setExtrude] = useState(5);
  if (!selection) return null;

  if (selection.error) {
    return (
      <div className="selpanel">
        <div className="selpanel-head">
          <span className="selpanel-title">selection</span>
          <button className="selpanel-close" onClick={clear}>
            ×
          </button>
        </div>
        <div className="selpanel-error">{selection.error}</div>
      </div>
    );
  }

  const props = selection.properties ?? {};
  return (
    <div className="selpanel">
      <div className="selpanel-head">
        <span className="selpanel-title">
          {selection.description ?? selection.kind} <span className="selpanel-kind">#{selection.index}</span>
        </span>
        <button className="selpanel-close" onClick={clear}>
          ×
        </button>
      </div>

      {selection.selector ? (
        <div className="selpanel-selector">
          <code>{selection.selector}</code>
          <span className={`selpanel-conf conf-${selection.selector_confidence}`}>{selection.selector_confidence}</span>
        </div>
      ) : null}

      {selection.selector ? (
        <div className="selpanel-gesture">
          <div className="gesture-amount">
            <input
              className="gesture-slider"
              type="range"
              min={-20}
              max={40}
              step={0.5}
              value={extrude}
              onChange={(e) => setExtrude(parseFloat(e.target.value))}
            />
            <span className="gesture-val">{extrude}mm</span>
          </div>
          <div className="gesture-ops">
            {selection.kind === "face" && (
              <button
                className="gesture-go"
                onClick={() =>
                  sendChat(
                    `Extrude the face selected via \`${selection.selector}\` (${selection.description ?? "face"}) ` +
                      `outward by ${extrude}mm and fuse the new material to the part. Keep everything else unchanged.`,
                  )
                }
              >
                extrude
              </button>
            )}
            <button
              className="gesture-go"
              onClick={() =>
                sendChat(
                  `Add a ${Math.abs(extrude)}mm fillet to the ${selection.kind} selected via \`${selection.selector}\` ` +
                    `(${selection.description ?? ""}). Keep everything else unchanged.`,
                )
              }
            >
              fillet
            </button>
            <button
              className="gesture-go"
              onClick={() =>
                sendChat(
                  `Add a ${Math.abs(extrude)}mm chamfer to the ${selection.kind} selected via \`${selection.selector}\` ` +
                    `(${selection.description ?? ""}). Keep everything else unchanged.`,
                )
              }
            >
              chamfer
            </button>
          </div>
        </div>
      ) : null}

      <dl className="selpanel-props">
        {Object.entries(props).map(([k, v]) => (
          <div key={k} className="selpanel-row">
            <dt>{k}</dt>
            <dd>{fmt(v)}</dd>
          </div>
        ))}
        {selection.solid?.volume != null && (
          <div className="selpanel-row">
            <dt>solid volume</dt>
            <dd>{fmt(selection.solid.volume)}</dd>
          </div>
        )}
        {selection.solid?.bbox && (
          <div className="selpanel-row">
            <dt>solid size</dt>
            <dd>{fmt(selection.solid.bbox.size)}</dd>
          </div>
        )}
      </dl>
    </div>
  );
}
