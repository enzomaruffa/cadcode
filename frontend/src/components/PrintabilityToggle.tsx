import { useStore } from "../lib/store";

export function PrintabilityToggle() {
  const viewMode = useStore((s) => s.viewMode);
  const setViewMode = useStore((s) => s.setViewMode);
  const printStats = useStore((s) => s.printStats);
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
    </div>
  );
}
