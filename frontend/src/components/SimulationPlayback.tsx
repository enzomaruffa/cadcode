import { useStore } from "../lib/store";

// Motion transport: play/pause + scrub through the swept frames, with a live
// readout and the worst-case / collision summary. Two sources share this UI:
// the kernel's motion(t) simulate op (clearance in mm) and require_motion
// sweeps (boolean contact in mm³, shown as "contact"). The actual pose
// animation (setPose per frame) is driven from Viewport, which owns the viewer
// handle; this component only drives the store's frame cursor.
export function SimulationPlayback() {
  const frames = useStore((s) => s.simFrames);
  const frame = useStore((s) => s.simFrame);
  const playing = useStore((s) => s.simPlaying);
  const summary = useStore((s) => s.simSummary);
  const specs = useStore((s) => s.simSpecs);
  const kind = useStore((s) => s.simKind);
  const sweeps = useStore((s) => s.motionSweeps);
  const activeSweep = useStore((s) => s.activeSweep);
  const setActiveSweep = useStore((s) => s.setActiveSweep);
  const setSimFrame = useStore((s) => s.setSimFrame);
  const toggleSimPlay = useStore((s) => s.toggleSimPlay);

  if (!frames.length) {
    return (
      <div className="viewmode-legend">
        <div className="legend-stats">
          add a <code>require_motion(part, turn(...), ...)</code> spec — or define <code>motion(t)</code> returning{" "}
          {"{name: Location}"} — to animate this assembly
        </div>
      </div>
    );
  }

  const cur = frames[Math.min(frame, frames.length - 1)];
  const worst = summary?.min_clearance_through_motion;
  const collides = (summary?.collision_frames?.length ?? 0) > 0;
  const failed = specs.filter((s) => !s.passed);
  const isSweep = kind === "sweep";
  const sweep = isSweep ? sweeps[activeSweep] : null;

  return (
    <div className="viewmode-legend">
      {isSweep && sweeps.length > 1 && (
        <div className="legend-row" style={{ marginBottom: 4 }}>
          <select
            className="sweep-select"
            value={activeSweep}
            onChange={(e) => setActiveSweep(parseInt(e.target.value, 10))}
            title="Pick which require_motion sweep to scrub"
          >
            {sweeps.map((sw, i) => (
              <option key={i} value={i}>
                {sw.passed ? "✓" : "✗"} {sw.label}
              </option>
            ))}
          </select>
        </div>
      )}
      <div className="motion-playback">
        <button className="motion-play" onClick={toggleSimPlay} title={playing ? "Pause" : "Play"}>
          {playing ? "❚❚" : "▶"}
        </button>
        <input
          type="range"
          min={0}
          max={frames.length - 1}
          value={Math.min(frame, frames.length - 1)}
          onChange={(e) => setSimFrame(parseInt(e.target.value, 10))}
          title="Scrub the motion"
        />
        <span className="motion-clear">
          {frame + 1}/{frames.length}
        </span>
      </div>
      <div className="legend-row" style={{ marginTop: 4 }}>
        <span>{isSweep ? "contact" : "clearance"}</span>
        <b className={`motion-clear${cur.colliding.length ? " warn" : ""}`}>
          {isSweep
            ? cur.colliding.length
              ? `${(-cur.min_clearance).toFixed(2)} mm³ — over budget`
              : `${(-cur.min_clearance).toFixed(2)} mm³`
            : cur.colliding.length
              ? "collision"
              : `${cur.min_clearance.toFixed(2)} mm`}
        </b>
      </div>
      <div className="legend-stats">
        {isSweep
          ? sweep
            ? `worst contact ${sweep.worst_contact.toFixed(2)} mm³ at t=${sweep.worst_t.toFixed(2)} (budget ${sweep.max_contact.toFixed(2)})`
            : null
          : collides
            ? `collides on ${summary?.collision_frames.length}/${frames.length} frames`
            : `worst clearance ${worst != null ? `${worst.toFixed(2)} mm` : "—"} through the swing`}
      </div>
      {isSweep && sweep && (
        <div className="legend-stats" style={{ color: sweep.passed ? "#3fb950" : "#f85149" }}>
          {sweep.passed ? "✓" : "✗"} {sweep.label}
          {sweep.truncated ? " (truncated — spec failed)" : ""}
        </div>
      )}
      {failed.length > 0 && (
        <div className="legend-stats" style={{ color: "#f85149" }}>
          ✗ {failed.map((s) => s.message).join(" · ")}
        </div>
      )}
      {!isSweep && specs.length > 0 && failed.length === 0 && (
        <div className="legend-stats" style={{ color: "#3fb950" }}>
          ✓ motion specs pass
        </div>
      )}
    </div>
  );
}
