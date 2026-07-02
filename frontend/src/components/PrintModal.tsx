import { useEffect, useMemo, useState } from "react";
import { HTTP_URL } from "../config";
import type { Spec, TessShapes } from "../lib/protocol";
import { useStore } from "../lib/store";

interface ProjectTree {
  name: string;
  parts: string[];
  scenes: string[];
}
interface CatalogPart {
  name: string;
}
interface PlanStats {
  name: string;
  qty: number;
  orientation: string;
  support_area: number;
}
interface Plan {
  ok: boolean;
  error?: string;
  shapes?: TessShapes;
  specs?: Spec[];
  stats?: PlanStats[];
  fits?: boolean;
  plate?: { w: number; d: number; bed_w: number; bed_d: number };
}

// One printable item the user can add to the plate.
interface Row {
  key: string;
  label: string;
  project?: string;
  name: string;
}

// Print plating: pick parts + quantities, choose your bed size, and the backend
// auto-orients each part (least support) and packs the plate. The arranged plate
// renders in the viewport and downloads as one STL/3MF for the slicer.
export function PrintModal({ onClose }: { onClose: () => void }) {
  const renderShapes = useStore((s) => s.renderShapes);
  const activeProject = useStore((s) => s.activeProject);

  const [rows, setRows] = useState<Row[]>([]);
  const [qty, setQty] = useState<Record<string, number>>({});
  const [bedW, setBedW] = useState(220);
  const [bedD, setBedD] = useState(220);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [plan, setPlan] = useState<Plan | null>(null);

  useEffect(() => {
    let alive = true;
    Promise.all([
      fetch(`${HTTP_URL}/projects`).then((r) => r.json()),
      fetch(`${HTTP_URL}/library`).then((r) => r.json()),
    ])
      .then(([pd, ld]: [{ projects?: ProjectTree[] }, { parts?: CatalogPart[] }]) => {
        if (!alive) return;
        const out: Row[] = [];
        for (const p of pd.projects ?? []) {
          for (const part of p.parts) {
            out.push({ key: `p:${p.name}:${part}`, label: `${p.name} / ${part}`, project: p.name, name: part });
          }
        }
        for (const g of ld.parts ?? []) {
          out.push({ key: `g:${g.name}`, label: `lib / ${g.name}`, name: g.name });
        }
        // parts of the active project first — they're the likely print targets
        out.sort((a, b) => Number(b.project === activeProject) - Number(a.project === activeProject));
        setRows(out);
      })
      .catch(() => void 0);
    return () => {
      alive = false;
    };
  }, [activeProject]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const items = useMemo(
    () => rows.filter((r) => (qty[r.key] ?? 0) > 0).map((r) => ({ project: r.project, name: r.name, qty: qty[r.key] })),
    [rows, qty],
  );

  const bump = (key: string, delta: number) =>
    setQty((q) => ({ ...q, [key]: Math.max(0, Math.min(99, (q[key] ?? 0) + delta)) }));

  const doPlan = async () => {
    if (!items.length || busy) return;
    setBusy(true);
    setError(null);
    try {
      const r = await fetch(`${HTTP_URL}/print/plan`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ items, bed: { w: bedW, d: bedD } }),
      });
      const d: Plan = await r.json();
      if (d.ok && d.shapes) {
        setPlan(d);
        renderShapes(d.shapes, []); // show the arranged plate in the viewport
      } else {
        setError(d.error ?? "plan failed");
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const download = async (fmt: "stl" | "3mf") => {
    setBusy(true);
    setError(null);
    try {
      const r = await fetch(`${HTTP_URL}/print/export`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ items, bed: { w: bedW, d: bedD }, format: fmt }),
      });
      if (!r.ok) {
        setError(await r.text());
        return;
      }
      const blob = await r.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `plate.${fmt}`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal modal-help" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <span className="modal-title">print a plate</span>
          <button className="modal-close" onClick={onClose}>
            ×
          </button>
        </div>
        <div className="modal-body">
          <p className="help-intro">
            Pick parts and how many of each. Each part is auto-oriented for the <b>least support</b> and packed onto
            your bed; the arranged plate shows in the viewport and downloads as one file for your slicer.
          </p>

          <div className="print-bed">
            <span>bed</span>
            <input
              type="number"
              value={bedW}
              min={50}
              max={1000}
              onChange={(e) => setBedW(parseFloat(e.target.value) || 220)}
            />
            ×
            <input
              type="number"
              value={bedD}
              min={50}
              max={1000}
              onChange={(e) => setBedD(parseFloat(e.target.value) || 220)}
            />
            <span>mm</span>
          </div>

          <div className="print-list">
            {rows.length === 0 && (
              <div className="lib-empty">No parts yet — save a part to a project or the library first.</div>
            )}
            {rows.map((r) => (
              <div className="print-row" key={r.key}>
                <span className="print-name">{r.label}</span>
                <span className="print-qty">
                  <button onClick={() => bump(r.key, -1)} disabled={(qty[r.key] ?? 0) === 0}>
                    −
                  </button>
                  <b>{qty[r.key] ?? 0}</b>
                  <button onClick={() => bump(r.key, +1)}>+</button>
                </span>
              </div>
            ))}
          </div>

          {error && <div className="print-error">{error}</div>}

          {plan?.stats && (
            <div className="print-result">
              {plan.stats.map((s) => (
                <div className="print-stat" key={s.name}>
                  <span>
                    {s.qty}× {s.name}
                  </span>
                  <span>
                    {s.orientation === "as-is" ? "as modeled" : `rotated ${s.orientation}`}
                    {s.support_area === 0 ? " · no support 🎉" : ` · ~${s.support_area} mm² support`}
                  </span>
                </div>
              ))}
              <div className={plan.fits ? "print-fit ok" : "print-fit bad"}>
                plate {plan.plate?.w}×{plan.plate?.d} mm on a {plan.plate?.bed_w}×{plan.plate?.bed_d} bed —{" "}
                {plan.fits ? "fits ✓" : "does NOT fit — reduce quantities or split into two plates"}
              </div>
            </div>
          )}

          <div className="colors-actions">
            <button className="btn btn-accept" onClick={doPlan} disabled={busy || items.length === 0}>
              {busy ? "working…" : plan ? "re-plan" : "plan print"}
            </button>
            {plan?.ok && (
              <>
                <button className="btn" onClick={() => download("stl")} disabled={busy}>
                  ⬇ STL
                </button>
                <button className="btn" onClick={() => download("3mf")} disabled={busy}>
                  ⬇ 3MF
                </button>
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
