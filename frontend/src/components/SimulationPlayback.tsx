import { useStore } from "../lib/store";

// Motion-sim transport: play/pause + scrub through the swept frames, with a live
// clearance readout and the worst-case / collision summary. The actual pose
// animation (setPose per frame) is driven from Viewport, which owns the viewer
// handle; this component only drives the store's frame cursor.
export function SimulationPlayback() {
  const frames = useStore((s) => s.simFrames);
  const frame = useStore((s) => s.simFrame);
  const playing = useStore((s) => s.simPlaying);
  const summary = useStore((s) => s.simSummary);
  const specs = useStore((s) => s.simSpecs);
  const setSimFrame = useStore((s) => s.setSimFrame);
  const toggleSimPlay = useStore((s) => s.toggleSimPlay);

  if (!frames.length) {
    return (
      <div className="viewmode-legend">
        <div className="legend-stats">
          define a <code>motion(t)</code> returning {"{name: Location}"} to animate joints
        </div>
      </div>
    );
  }

  const cur = frames[Math.min(frame, frames.length - 1)];
  const worst = summary?.min_clearance_through_motion;
  const collides = (summary?.collision_frames?.length ?? 0) > 0;
  const failed = specs.filter((s) => !s.passed);

  return (
    <div className="viewmode-legend">
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
        <span>clearance</span>
        <b className={`motion-clear${cur.colliding.length ? " warn" : ""}`}>
          {cur.colliding.length ? "collision" : `${cur.min_clearance.toFixed(2)} mm`}
        </b>
      </div>
      <div className="legend-stats">
        {collides
          ? `collides on ${summary?.collision_frames.length}/${frames.length} frames`
          : `worst clearance ${worst != null ? `${worst.toFixed(2)} mm` : "—"} through the swing`}
      </div>
      {failed.length > 0 && (
        <div className="legend-stats" style={{ color: "#f85149" }}>
          ✗ {failed.map((s) => s.message).join(" · ")}
        </div>
      )}
      {specs.length > 0 && failed.length === 0 && (
        <div className="legend-stats" style={{ color: "#3fb950" }}>
          ✓ motion specs pass
        </div>
      )}
    </div>
  );
}
