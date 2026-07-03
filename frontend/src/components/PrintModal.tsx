import { useEffect, useMemo, useState } from "react";
import { HTTP_URL } from "../config";
import type { Spec, TessShapes } from "../lib/protocol";
import { useStore } from "../lib/store";

interface Candidate {
  project?: string | null;
  name: string;
  label: string;
}
interface PlanStats {
  name: string;
  qty: number;
  orientation: string;
  support_area: number;
  est_min?: number;
}
interface PlateInfo {
  index: number;
  w: number;
  d: number;
  bed_w: number;
  bed_d: number;
  est_min?: number;
}
interface Plan {
  ok: boolean;
  error?: string;
  shapes?: TessShapes;
  specs?: Spec[];
  stats?: PlanStats[];
  fits?: boolean;
  plates?: PlateInfo[];
  slicer?: boolean; // prusa-slicer available server-side for exact times
}

function fmtMin(min: number | undefined): string {
  if (min == null) return "";
  if (min < 60) return `${Math.round(min)}min`;
  return `${Math.floor(min / 60)}h ${String(Math.round(min % 60)).padStart(2, "0")}m`;
}

// One printable item the user can add to the plate.
interface Row {
  key: string;
  label: string;
  project?: string;
  name: string;
}

type Strategy = "material" | "plates" | "fastest";

const STRATEGIES: { key: Strategy; label: string; hint: string }[] = [
  { key: "material", label: "least material", hint: "orient every part for the least support waste" },
  { key: "plates", label: "fewest plates", hint: "orient for the smallest footprints so more parts share a bed" },
  { key: "fastest", label: "fastest print", hint: "orient for the least estimated print time (walls+infill+support)" },
];

// Print plating: pick parts + quantities, a bed size, and a strategy; the
// backend auto-orients each part for that objective and packs as FEW plates as
// needed. Renders in the viewport; each plate downloads as STL/3MF.
export function PrintModal({ onClose }: { onClose: () => void }) {
  const renderShapes = useStore((s) => s.renderShapes);
  const activeProject = useStore((s) => s.activeProject);

  const [rows, setRows] = useState<Row[]>([]);
  const [qty, setQty] = useState<Record<string, number>>({});
  const [bedW, setBedW] = useState(220);
  const [bedD, setBedD] = useState(220);
  const [strategy, setStrategy] = useState<Strategy>("material");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [plan, setPlan] = useState<Plan | null>(null);
  // Exact slicer times, keyed by plate index (-1 = whole single plate).
  const [exact, setExact] = useState<Record<number, number | "working">>({});
  const [calN, setCalN] = useState(0); // how many real slices the estimator learned from

  const refreshCal = () =>
    fetch(`${HTTP_URL}/print/calibration`)
      .then((r) => r.json())
      .then((d: { samples?: number }) => setCalN(d.samples ?? 0))
      .catch(() => void 0);
  useEffect(() => {
    refreshCal();
  }, []);

  // Candidates are project-scoped: the active project's own parts + any part it
  // imports (cross-project or lib). No project → everything.
  useEffect(() => {
    let alive = true;
    fetch(`${HTTP_URL}/print/parts?project=${encodeURIComponent(activeProject ?? "")}`)
      .then((r) => r.json())
      .then((d: { parts?: Candidate[] }) => {
        if (!alive) return;
        setRows(
          (d.parts ?? []).map((c) => ({
            key: `${c.project ?? ""}:${c.name}`,
            label: c.label,
            project: c.project ?? undefined,
            name: c.name,
          })),
        );
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
        body: JSON.stringify({ items, bed: { w: bedW, d: bedD }, strategy }),
      });
      const d: Plan = await r.json();
      if (d.ok && d.shapes) {
        setPlan(d);
        setExact({});
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

  // Ask the server to actually SLICE a plate (PrusaSlicer) for the exact time.
  const sliceExact = async (plate?: number) => {
    const key = plate ?? -1;
    setExact((e) => ({ ...e, [key]: "working" }));
    try {
      const r = await fetch(`${HTTP_URL}/print/slice`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ items, bed: { w: bedW, d: bedD }, strategy, plate }),
      });
      const d: { ok?: boolean; minutes?: number; error?: string } = await r.json();
      if (d.ok && d.minutes != null) {
        setExact((e) => ({ ...e, [key]: d.minutes! }));
        refreshCal(); // this slice just taught the estimator
      } else {
        setExact((e) => {
          const { [key]: _drop, ...rest } = e;
          return rest;
        });
        setError(d.error ?? "slicing failed");
      }
    } catch (e) {
      setError(String(e));
    }
  };

  const download = async (fmt: "stl" | "3mf", plate?: number) => {
    setBusy(true);
    setError(null);
    try {
      const r = await fetch(`${HTTP_URL}/print/export`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ items, bed: { w: bedW, d: bedD }, strategy, format: fmt, plate }),
      });
      if (!r.ok) {
        setError(await r.text());
        return;
      }
      const blob = await r.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = plate !== undefined ? `plate${plate + 1}.${fmt}` : `plate.${fmt}`;
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
            Pick parts and how many of each. Each part is auto-oriented for your chosen goal and packed onto as few
            plates as needed; the plates render in the viewport and each downloads as one file for your slicer.{" "}
            {calN > 0
              ? `Time estimates are calibrated from ${calN} real slice${calN > 1 ? "s" : ""}; hit "exact?" for the slicer's own number.`
              : `Times are quick estimates — hit "exact?" on a plate for the real slicer number (which also trains the estimates).`}
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

          <div className="scope-toggle print-strategy">
            {STRATEGIES.map((s) => (
              <button
                key={s.key}
                className={strategy === s.key ? "on" : ""}
                onClick={() => setStrategy(s.key)}
                title={s.hint}
              >
                {s.label}
              </button>
            ))}
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
                    {s.est_min != null ? ` · ~${fmtMin(s.est_min)} each` : ""}
                  </span>
                </div>
              ))}
              <div className={plan.fits ? "print-fit ok" : "print-fit bad"}>
                {(plan.plates?.length ?? 1) === 1
                  ? `one plate, ${plan.plates?.[0]?.w}×${plan.plates?.[0]?.d} mm on a ${plan.plates?.[0]?.bed_w}×${plan.plates?.[0]?.bed_d} bed`
                  : `split across ${plan.plates?.length} plates (each fits your ${plan.plates?.[0]?.bed_w}×${plan.plates?.[0]?.bed_d} bed)`}
                {plan.fits ? " ✓" : " — a part is bigger than the bed itself!"}
                {(plan.plates?.length ?? 1) === 1 && plan.plates?.[0]?.est_min != null && (
                  <>
                    {" · "}
                    {exact[-1] === "working"
                      ? "slicing…"
                      : exact[-1] != null
                        ? `${fmtMin(exact[-1] as number)} (slicer)`
                        : `~${fmtMin(plan.plates[0].est_min)}`}
                    {plan.slicer && exact[-1] == null && (
                      <button
                        className="print-exact"
                        onClick={() => sliceExact(undefined)}
                        title="Run PrusaSlicer for the exact time"
                      >
                        exact?
                      </button>
                    )}
                  </>
                )}
              </div>
              {(plan.plates?.length ?? 0) > 1 &&
                plan.plates?.map((p) => (
                  <div className="print-plate-dl" key={p.index}>
                    <span>
                      plate {p.index + 1} · {p.w}×{p.d} mm
                      {p.est_min != null && (
                        <>
                          {" · "}
                          {exact[p.index] === "working"
                            ? "slicing…"
                            : exact[p.index] != null
                              ? `${fmtMin(exact[p.index] as number)} (slicer)`
                              : `~${fmtMin(p.est_min)}`}
                          {plan.slicer && exact[p.index] == null && (
                            <button
                              className="print-exact"
                              onClick={() => sliceExact(p.index)}
                              title="Run PrusaSlicer for the exact time"
                            >
                              exact?
                            </button>
                          )}
                        </>
                      )}
                    </span>
                    <span>
                      <button className="btn" onClick={() => download("stl", p.index)} disabled={busy}>
                        ⬇ STL
                      </button>
                      <button className="btn" onClick={() => download("3mf", p.index)} disabled={busy}>
                        ⬇ 3MF
                      </button>
                    </span>
                  </div>
                ))}
            </div>
          )}

          <div className="colors-actions">
            <button className="btn btn-accept" onClick={doPlan} disabled={busy || items.length === 0}>
              {busy ? "working…" : plan ? "re-plan" : "plan print"}
            </button>
            {plan?.ok && (plan.plates?.length ?? 1) === 1 && (
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
