import { useStore } from "../lib/store";

export function PrintabilityToggle() {
  const viewMode = useStore((s) => s.viewMode);
  const setViewMode = useStore((s) => s.setViewMode);
  const printStats = useStore((s) => s.printStats);
  const buildAxis = useStore((s) => s.buildAxis);
  const setBuildAxis = useStore((s) => s.setBuildAxis);

  return (
    <div className="viewmode">
      <div className="viewmode-toggle">
        <button className={viewMode === "technical" ? "on" : ""} onClick={() => setViewMode("technical")}>
          technical
        </button>
        <button className={viewMode === "printability" ? "on" : ""} onClick={() => setViewMode("printability")}>
          printability
        </button>
      </div>
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
