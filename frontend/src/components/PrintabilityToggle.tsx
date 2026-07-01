import { useStore } from "../lib/store";

export function PrintabilityToggle() {
  const viewMode = useStore((s) => s.viewMode);
  const setViewMode = useStore((s) => s.setViewMode);
  const printStats = useStore((s) => s.printStats);
  const physical = useStore((s) => s.physical);
  const buildAxis = useStore((s) => s.buildAxis);
  const setBuildAxis = useStore((s) => s.setBuildAxis);
  const presentation = useStore((s) => s.presentation);
  const togglePresentation = useStore((s) => s.togglePresentation);

  return (
    <div className="viewmode">
      <div className="viewmode-toggle">
        <button className={viewMode === "technical" ? "on" : ""} onClick={() => setViewMode("technical")}>
          technical
        </button>
        <button className={viewMode === "highlight" ? "on" : ""} onClick={() => setViewMode("highlight")}>
          highlight
        </button>
        <button className={viewMode === "printability" ? "on" : ""} onClick={() => setViewMode("printability")}>
          printability
        </button>
        <button className={viewMode === "physical" ? "on" : ""} onClick={() => setViewMode("physical")}>
          physical
        </button>
        <button
          className={presentation ? "on" : ""}
          onClick={togglePresentation}
          title="Pretty render — PBR lighting, ambient occlusion & soft shadows (vs. flat technical shading)"
        >
          ✨ pretty
        </button>
      </div>
      {viewMode === "highlight" && (
        <div className="viewmode-legend">
          <span style={{ color: "#ffd23f" }}>●</span> faces from the cursor's line · click a face to jump to its code
        </div>
      )}
      {viewMode === "printability" && (
        <div className="viewmode-legend">
          <div className="legend-build">
            build&nbsp;
            {(["Z", "X", "Y"] as const).map((a) => (
              <button key={a} className={buildAxis === a ? "on" : ""} onClick={() => setBuildAxis(a)}>
                {a}↑
              </button>
            ))}
          </div>
          <div className="legend-row">
            <span className="swatch" style={{ background: "#3fb950" }} /> ok
          </div>
          <div className="legend-row">
            <span className="swatch" style={{ background: "#ffd222" }} /> ≤{printStats?.limit ?? 45}° overhang
          </div>
          <div className="legend-row">
            <span className="swatch" style={{ background: "#f85149" }} /> needs support
          </div>
          {printStats && (
            <div className="legend-stats">
              {printStats.needs_support}/{printStats.faces} faces need support · build {printStats.build_axis}↑
            </div>
          )}
        </div>
      )}
      {viewMode === "physical" && physical && (
        <div className="viewmode-legend physical-readout">
          <div className="readout-row">
            <span>mass</span>
            <b>{physical.mass_g.toFixed(1)} g</b>
          </div>
          <div className="readout-row">
            <span>volume</span>
            <b>{physical.volume_cm3.toFixed(2)} cm³</b>
          </div>
          <div className="readout-row">
            <span>material</span>
            <b>{physical.material_name}</b>
          </div>
          <div className="readout-row">
            <span>cost</span>
            <b>${physical.cost.toFixed(2)}</b>
          </div>
          <div className="readout-row">
            <span>filament</span>
            <b>{physical.filament_len_mm != null ? `${(physical.filament_len_mm / 1000).toFixed(2)} m` : "—"}</b>
          </div>
          <div className="readout-row">
            <span>print time</span>
            <b>~{formatMinutes(physical.print_time_min)}</b>
          </div>
          <div className="readout-row">
            <span>stability</span>
            <b style={{ color: physical.stable ? "#3fb950" : "#f85149" }}>
              {physical.tip_angle == null
                ? "unstable"
                : physical.stable
                  ? `tips @ ${physical.tip_angle.toFixed(0)}°`
                  : "tips over"}
            </b>
          </div>
          <div className="readout-row">
            <span>floats</span>
            <b>{physical.floats ? "yes" : "no"}</b>
          </div>
          <div className="legend-stats">
            COM ({physical.com.map((v) => v.toFixed(1)).join(", ")}) · time is an estimate
          </div>
        </div>
      )}
    </div>
  );
}

function formatMinutes(min: number): string {
  if (min < 60) return `${Math.round(min)} min`;
  const h = Math.floor(min / 60);
  const m = Math.round(min % 60);
  return `${h}h ${m}m`;
}
