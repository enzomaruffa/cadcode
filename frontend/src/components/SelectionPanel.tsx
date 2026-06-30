import { useStore } from "../lib/store";

function fmt(v: unknown): string {
  if (Array.isArray(v)) return `(${v.map((x) => (typeof x === "number" ? x.toFixed(2) : String(x))).join(", ")})`;
  if (typeof v === "number") return v.toFixed(3);
  return String(v);
}

export function SelectionPanel() {
  const selection = useStore((s) => s.selection);
  const clear = useStore((s) => s.clearSelection);
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
