import { useEffect, useMemo, useRef, useState } from "react";
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
  needs_support?: boolean;
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
  supports?: boolean; // whether this plan was arranged with support material on
  any_needs_support?: boolean; // some part still overhangs at its best orientation
  fits?: boolean;
  total_min?: number; // shortest-path time to print ALL plates on one printer (support incl.)
  plates?: PlateInfo[];
  slicer?: boolean; // a real slicer is available server-side for exact times
  slicer_name?: string; // which engine: "orca" | "prusa" | "none"
}

// A finished slice: exact time + filament + whether the slicer actually laid support.
interface SliceResult {
  minutes: number;
  filament_cm3?: number;
  supported?: boolean;
  slicer?: string; // engine that produced this number ("orca" | "prusa")
}

function fmtMin(min: number | undefined): string {
  if (min == null) return "";
  if (min < 60) return `${Math.round(min)}min`;
  return `${Math.floor(min / 60)}h ${String(Math.round(min % 60)).padStart(2, "0")}m`;
}

function fmtCm3(v: number | undefined): string {
  if (v == null || v <= 0) return "";
  return `${v.toFixed(1)} cm³`;
}

// A supported printer from OrcaSlicer's catalog (picking one prefills the bed).
interface Printer {
  name: string;
  vendor: string;
  bed_w?: number | null;
  bed_d?: number | null;
  nozzle?: string | null;
}
interface Filament {
  name: string;
  vendor: string;
  type: string;
}

// One printable item the user can add to the plate.
interface Row {
  key: string;
  label: string;
  project?: string;
  name: string;
}

const LS_PRINTER = "cadcode.print.printer";
const LS_FILAMENT = "cadcode.print.filament";

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
  const [supports, setSupports] = useState(true); // auto-add support material where overhangs need it
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [plan, setPlan] = useState<Plan | null>(null);
  // Exact slicer results (time + filament), keyed by plate index (-1 = whole single plate).
  const [exact, setExact] = useState<Record<number, SliceResult | "working">>({});
  const [calN, setCalN] = useState(0); // how many real slices the estimator learned from
  const planToken = useRef(0); // bump per plan → in-flight background slices from an old plan are dropped

  // Printer + filament (OrcaSlicer catalog). Picking a printer prefills the bed;
  // both relay to the slicer so numbers match a real machine. Persisted so the
  // user's Bambu (etc.) sticks across sessions.
  const [printers, setPrinters] = useState<Printer[]>([]);
  const [printer, setPrinter] = useState<string>(() => localStorage.getItem(LS_PRINTER) ?? "");
  const [filaments, setFilaments] = useState<Filament[]>([]);
  const [filament, setFilament] = useState<string>(() => localStorage.getItem(LS_FILAMENT) ?? "");
  const curPrinter = useMemo(() => printers.find((p) => p.name === printer), [printers, printer]);
  const vendors = useMemo(() => [...new Set(printers.map((p) => p.vendor))].sort(), [printers]);
  const models = useMemo(
    () => printers.filter((p) => p.vendor === curPrinter?.vendor).sort((a, b) => a.name.localeCompare(b.name)),
    [printers, curPrinter?.vendor],
  );
  const filamentVendors = useMemo(() => [...new Set(filaments.map((f) => f.vendor))], [filaments]);

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

  // Select a printer + prefill its bed (still tweakable below). Kept out of an
  // effect so the bed only snaps to the printer on an explicit pick, and lint's
  // set-state-in-effect rule stays happy.
  const pickPrinter = (p: Printer | undefined) => {
    if (!p) return;
    setPrinter(p.name);
    if (p.bed_w && p.bed_d) {
      setBedW(Math.round(p.bed_w));
      setBedD(Math.round(p.bed_d));
    }
  };

  // Load the printer catalog once (empty when Orca isn't the slicer). Pick the
  // saved printer if still valid, else the server default (a Bambu Lab).
  useEffect(() => {
    let alive = true;
    fetch(`${HTTP_URL}/print/printers`)
      .then((r) => r.json())
      .then((d: { printers?: Printer[]; default?: string | null }) => {
        if (!alive || !d.printers?.length) return;
        setPrinters(d.printers);
        const saved = localStorage.getItem(LS_PRINTER);
        const chosen = saved && d.printers.some((p) => p.name === saved) ? saved : (d.default ?? d.printers[0].name);
        pickPrinter(d.printers.find((p) => p.name === chosen));
      })
      .catch(() => void 0);
    return () => {
      alive = false;
    };
  }, []);

  // Filaments are scoped to the chosen printer (generics + printer-compatible) —
  // the full catalog is ~6k. Refetch on printer change; keep the saved filament.
  useEffect(() => {
    if (!printer) return;
    let alive = true;
    fetch(`${HTTP_URL}/print/filaments?printer=${encodeURIComponent(printer)}`)
      .then((r) => r.json())
      .then((d: { filaments?: Filament[]; default?: string | null }) => {
        if (!alive || !d.filaments) return;
        setFilaments(d.filaments);
        setFilament((cur) =>
          cur && d.filaments!.some((f) => f.name === cur) ? cur : (d.default ?? d.filaments![0]?.name ?? ""),
        );
      })
      .catch(() => void 0);
    return () => {
      alive = false;
    };
  }, [printer]);

  useEffect(() => {
    if (printer) localStorage.setItem(LS_PRINTER, printer);
  }, [printer]);
  useEffect(() => {
    if (filament) localStorage.setItem(LS_FILAMENT, filament);
  }, [filament]);

  const items = useMemo(
    () => rows.filter((r) => (qty[r.key] ?? 0) > 0).map((r) => ({ project: r.project, name: r.name, qty: qty[r.key] })),
    [rows, qty],
  );

  const bump = (key: string, delta: number) =>
    setQty((q) => ({ ...q, [key]: Math.max(0, Math.min(99, (q[key] ?? 0) + delta)) }));

  // Changing supports makes the current slicer numbers stale — drop them (and any
  // in-flight background slice) so the estimate shows until the user re-plans.
  const changeSupports = (on: boolean) => {
    if (on === supports) return;
    setSupports(on);
    planToken.current++;
    setExact({});
  };

  const doPlan = async () => {
    if (!items.length || busy) return;
    setBusy(true);
    setError(null);
    try {
      const r = await fetch(`${HTTP_URL}/print/plan`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ items, bed: { w: bedW, d: bedD }, strategy, supports }),
      });
      const d: Plan = await r.json();
      if (d.ok && d.shapes) {
        setPlan(d);
        setExact({});
        renderShapes(d.shapes, []); // show the arranged plate in the viewport
        // Auto-run the real slicer per plate in the background → the "~" estimates
        // get replaced with exact numbers. A new plan bumps the token so stale
        // slices are dropped.
        if (d.slicer) {
          const token = ++planToken.current;
          void autoSlice(d, token);
        }
      } else {
        setError(d.error ?? "plan failed");
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  // Slice one plate with PrusaSlicer for the exact time. `token` guards against a
  // superseded plan (null = manual button, always applies + surfaces errors).
  const sliceOne = async (plate: number | undefined, token: number | null) => {
    const key = plate ?? -1;
    const live = () => token === null || token === planToken.current;
    setExact((e) => ({ ...e, [key]: "working" }));
    try {
      const r = await fetch(`${HTTP_URL}/print/slice`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ items, bed: { w: bedW, d: bedD }, strategy, supports, printer, filament, plate }),
      });
      const d: {
        ok?: boolean;
        minutes?: number;
        filament_cm3?: number;
        supported?: boolean;
        slicer?: string;
        error?: string;
      } = await r.json();
      if (!live()) return; // a newer plan superseded this slice
      if (d.ok && d.minutes != null) {
        setExact((e) => ({
          ...e,
          [key]: { minutes: d.minutes!, filament_cm3: d.filament_cm3, supported: d.supported, slicer: d.slicer },
        }));
        if (supports) refreshCal(); // supported slices teach the estimator
      } else {
        setExact((e) => {
          const { [key]: _drop, ...rest } = e;
          return rest;
        });
        if (token === null) setError(d.error ?? "slicing failed");
      }
    } catch (e) {
      if (live()) {
        setExact((e2) => {
          const { [key]: _drop, ...rest } = e2;
          return rest;
        });
        if (token === null) setError(String(e));
      }
    }
  };

  const sliceExact = (plate?: number) => void sliceOne(plate, null);

  // Background: slice every plate of a fresh plan, one at a time (slicing is
  // CPU-heavy), stopping early if a newer plan took over.
  const autoSlice = async (d: Plan, token: number) => {
    const plates = d.plates ?? [];
    if (plates.length <= 1) {
      await sliceOne(undefined, token);
      return;
    }
    for (const p of plates) {
      if (token !== planToken.current) return;
      await sliceOne(p.index, token);
    }
  };

  const download = async (fmt: "stl" | "3mf", plate?: number) => {
    setBusy(true);
    setError(null);
    try {
      const r = await fetch(`${HTTP_URL}/print/export`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ items, bed: { w: bedW, d: bedD }, strategy, supports, format: fmt, plate }),
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

  // Time + filament for a plate: "slicing…" while working, then the exact slicer
  // number (with filament, and a note when support was actually laid), else the
  // instant "~estimate" with an on-demand "exact?" button.
  const renderTime = (key: number, estMin: number) => {
    const res = exact[key];
    if (res === "working") return <>slicing…</>;
    if (res != null) {
      return (
        <>
          {fmtMin(res.minutes)} ({res.slicer ?? plan?.slicer_name ?? "slicer"})
          {res.filament_cm3 ? ` · ${fmtCm3(res.filament_cm3)}` : ""}
          {res.supported ? " · incl. support" : ""}
        </>
      );
    }
    return (
      <>
        ~{fmtMin(estMin)}
        {plan?.slicer && (
          <button
            className="print-exact"
            onClick={() => sliceExact(key < 0 ? undefined : key)}
            title="Run PrusaSlicer for the exact time"
          >
            exact?
          </button>
        )}
      </>
    );
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
            plates as needed; the plates render in the viewport and each downloads as one file for your slicer. Times
            show a quick estimate instantly, then the <b>real slicer</b> runs in the background and replaces them with
            exact numbers (including filament and any support material).
            {calN > 0 ? ` Estimates are self-calibrating (${calN} slices learned so far).` : ""}
          </p>

          {printers.length > 0 && (
            <>
              <div className="print-picker">
                <span>printer</span>
                <select
                  className="print-select"
                  value={curPrinter?.vendor ?? ""}
                  onChange={(e) => pickPrinter(printers.find((p) => p.vendor === e.target.value))}
                  title="Printer brand"
                >
                  {vendors.map((v) => (
                    <option key={v} value={v}>
                      {v}
                    </option>
                  ))}
                </select>
                <select
                  className="print-select grow"
                  value={printer}
                  onChange={(e) => pickPrinter(printers.find((p) => p.name === e.target.value))}
                  title="Printer model — prefills the bed size"
                >
                  {models.map((p) => (
                    <option key={p.name} value={p.name}>
                      {p.name.replace(`${p.vendor} `, "")}
                      {p.bed_w ? ` — ${Math.round(p.bed_w)}×${Math.round(p.bed_d ?? p.bed_w)}` : ""}
                    </option>
                  ))}
                </select>
              </div>
              <div className="print-picker">
                <span>filament</span>
                <select
                  className="print-select grow"
                  value={filament}
                  onChange={(e) => setFilament(e.target.value)}
                  title="Filament preset (tweak temps in your slicer if needed)"
                >
                  {filamentVendors.map((v) => (
                    <optgroup key={v} label={v}>
                      {filaments
                        .filter((f) => f.vendor === v)
                        .map((f) => (
                          <option key={f.name} value={f.name}>
                            {f.name} · {f.type}
                          </option>
                        ))}
                    </optgroup>
                  ))}
                </select>
              </div>
            </>
          )}

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
            <span>mm{curPrinter ? " (from printer — tweakable)" : ""}</span>
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

          <div className="scope-toggle print-strategy">
            <button
              className={supports ? "on" : ""}
              onClick={() => changeSupports(true)}
              title="Let the slicer auto-add support material wherever overhangs need it (safe default)."
            >
              auto supports
            </button>
            <button
              className={!supports ? "on" : ""}
              onClick={() => changeSupports(false)}
              title="Print without support — cleaner & faster, but overhangs may sag. Parts are still oriented to minimize overhang."
            >
              no supports
            </button>
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
                    {s.support_area === 0
                      ? " · no overhang 🎉"
                      : plan.supports === false
                        ? ` · ⚠ ${s.support_area} mm² overhang, unsupported`
                        : ` · ~${s.support_area} mm² support`}
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
                    {renderTime(-1, plan.plates[0].est_min)}
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
                          {renderTime(p.index, p.est_min)}
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
              {/* Grand total across all plates — the shortest-path time to print
                  everything on one printer, support included. For >1 plate more
                  plates cost MORE (each re-pays its layer overhead); support is
                  the same however parts are split, so this is the honest number. */}
              {(plan.plates?.length ?? 0) > 1 &&
                plan.total_min != null &&
                (() => {
                  const results = (plan.plates ?? []).map((p) => exact[p.index]);
                  const done = results.filter((r) => r != null && r !== "working") as SliceResult[];
                  const allDone = done.length === results.length && results.length > 0;
                  const slicerTotal = allDone ? done.reduce((a, r) => a + r.minutes, 0) : null;
                  const filTotal = allDone ? done.reduce((a, r) => a + (r.filament_cm3 ?? 0), 0) : 0;
                  return (
                    <div className="print-total">
                      <b>all {plan.plates?.length} plates</b>
                      {" · "}
                      {slicerTotal != null ? (
                        <>
                          {fmtMin(slicerTotal)} ({plan.slicer_name ?? "slicer"})
                          {filTotal > 0 ? ` · ${fmtCm3(filTotal)}` : ""}
                        </>
                      ) : (
                        <>
                          ~{fmtMin(plan.total_min)}
                          {plan.slicer ? " · slicing plates…" : ""}
                        </>
                      )}
                    </div>
                  );
                })()}
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
