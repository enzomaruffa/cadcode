import { useEffect, useRef, useState } from "react";
import { CadCanvas, type CadCanvasHandle, type PickEvent, type SectionAxis } from "@cadcode/viewer";
import { useStore } from "../lib/store";

// The viewport is now our own renderer (@cadcode/viewer). The store contract is
// unchanged: a pick becomes sendSelect(kind, shapeId, index); in highlight mode
// a pick reveals the source line instead. A small toolbar drives select/measure
// and section planes (imperatively, via the canvas handle).
export function Viewport() {
  const viewerRef = useRef<CadCanvasHandle>(null);
  const [mode, setMode] = useState<"select" | "measure">("select");
  const [axis, setAxis] = useState<SectionAxis | null>(null);
  const [offset, setOffset] = useState(0.5);

  const [grid, setGrid] = useState(true);
  const [physics, setPhysics] = useState(false);

  const shapes = useStore((s) => s.shapes);
  const rev = useStore((s) => s.geometryRev);
  const viewMode = useStore((s) => s.viewMode);
  const activeLine = useStore((s) => s.activeLine);
  const presentation = useStore((s) => s.presentation);
  const physical = useStore((s) => s.physical);
  const simFrames = useStore((s) => s.simFrames);
  const simFrame = useStore((s) => s.simFrame);
  const simPlaying = useStore((s) => s.simPlaying);

  // Motion playback: advance the frame cursor ~20fps while playing.
  useEffect(() => {
    if (viewMode !== "motion" || !simPlaying || simFrames.length < 2) return;
    let raf = 0;
    let last = 0;
    const FRAME_MS = 1000 / 20;
    const tick = (now: number) => {
      if (now - last >= FRAME_MS) {
        last = now;
        const s = useStore.getState();
        s.setSimFrame(s.simFrame + 1);
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [viewMode, simPlaying, simFrames]);

  // Apply the current frame's rigid poses + flash colliding parts red.
  useEffect(() => {
    if (viewMode !== "motion") return;
    const f = simFrames[simFrame];
    if (!f) return;
    viewerRef.current?.setPose(f.transforms);
    viewerRef.current?.flashLeaves(f.colliding, "#f85149");
  }, [viewMode, simFrame, simFrames, rev]);

  const onPick = (p: PickEvent) => {
    const st = useStore.getState();
    if (st.viewMode === "highlight") {
      const m = /L(\d+)__/.exec(p.name);
      if (m) st.setRevealLine(parseInt(m[1], 10));
      return;
    }
    st.sendSelect(p.kind, p.shapeId, p.index);
  };

  const applyMode = (m: "select" | "measure") => {
    setMode(m);
    viewerRef.current?.setMode(m);
  };
  const applySection = (a: SectionAxis | null, o: number) => {
    setAxis(a);
    viewerRef.current?.setSection(a, o);
  };

  return (
    <>
      <CadCanvas
        ref={viewerRef}
        className="viewport"
        shapes={shapes}
        geometryRev={rev}
        renderProfile={presentation ? "presentation" : "technical"}
        viewMode={viewMode}
        activeLine={activeLine}
        physical={physical}
        physics={physics}
        onPick={onPick}
      />
      <div className="vp-toolbar">
        <div className="vp-group">
          <button
            className={mode === "select" ? "on" : ""}
            onClick={() => applyMode("select")}
            title="Select faces/edges"
          >
            select
          </button>
          <button
            className={mode === "measure" ? "on" : ""}
            onClick={() => applyMode("measure")}
            title="Click two points to measure"
          >
            measure
          </button>
        </div>
        {mode === "measure" && (
          <button className="vp-btn" onClick={() => viewerRef.current?.clearMeasure()} title="Clear measurements">
            clear
          </button>
        )}
        <div className="vp-group" title="Section plane — slice the model along an axis">
          <span className="vp-label">cut</span>
          {(["x", "y", "z"] as const).map((a) => (
            <button
              key={a}
              className={axis === a ? "on" : ""}
              onClick={() => applySection(axis === a ? null : a, offset)}
              title={`Cut along ${a.toUpperCase()}`}
            >
              {a.toUpperCase()}
            </button>
          ))}
        </div>
        {axis && (
          <input
            type="range"
            className="vp-slider"
            min={0}
            max={1}
            step={0.01}
            value={offset}
            onChange={(e) => {
              const o = parseFloat(e.target.value);
              setOffset(o);
              applySection(axis, o);
            }}
            title="Section offset"
          />
        )}
        <button
          className={grid ? "vp-btn on" : "vp-btn"}
          onClick={() => {
            const next = !grid;
            setGrid(next);
            viewerRef.current?.setGrid(next);
          }}
          title="Toggle the ground grid"
        >
          grid
        </button>
        <button className="vp-btn" onClick={() => viewerRef.current?.fitView()} title="Fit view">
          fit
        </button>
        <div
          className="vp-group"
          title="Physics playground — parts fall under gravity; drag them (collisions respected)"
        >
          <button
            className={physics ? "on" : ""}
            onClick={() => setPhysics((p) => !p)}
            title="Toggle gravity + drag playground"
          >
            {physics ? "◼ physics" : "▶ physics"}
          </button>
          {physics && (
            <button onClick={() => viewerRef.current?.resetPhysics()} title="Drop parts back to their code positions">
              reset
            </button>
          )}
        </div>
      </div>
    </>
  );
}
