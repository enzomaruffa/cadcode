import { useEffect, useRef, useState } from "react";
import { useStore } from "../lib/store";

// Slider range heuristic around the current value.
function rangeFor(v: number): { min: number; max: number; step: number } {
  const mag = Math.max(Math.abs(v), 1);
  const max = Math.ceil(mag * 2.5);
  const min = v < 0 ? -max : 0;
  const step = Number.isInteger(v) ? 1 : mag < 10 ? 0.1 : 1;
  return { min, max, step };
}

export function ParamsPanel() {
  const params = useStore((s) => s.params);
  const setParam = useStore((s) => s.setParam);

  const [vals, setVals] = useState<Record<string, number>>({});
  const editedAt = useRef(0);

  // Sync slider values from source-derived params, but not right after a drag
  // (so an in-flight edit isn't clobbered by the round-trip echo). External
  // changes (agent edits) flow in once the user is idle.
  useEffect(() => {
    if (Date.now() - editedAt.current < 500) return;
    const next: Record<string, number> = {};
    for (const p of params) next[p.name] = p.value;
    setVals(next);
  }, [params]);

  if (params.length === 0) return null;

  return (
    <div className="params">
      <div className="params-title">parameters</div>
      <div className="params-grid">
        {params.map((p) => {
          const v = vals[p.name] ?? p.value;
          const { min, max, step } = rangeFor(p.value);
          return (
            <div key={p.name} className="param-row">
              <label className="param-name">{p.name}</label>
              <input
                className="param-slider"
                type="range"
                min={min}
                max={max}
                step={step}
                value={v}
                onChange={(e) => {
                  const nv = parseFloat(e.target.value);
                  editedAt.current = Date.now();
                  setVals((s) => ({ ...s, [p.name]: nv }));
                  setParam(p.line, p.name, nv);
                }}
              />
              <span className="param-val">{p.is_int ? v : v.toFixed(2)}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
