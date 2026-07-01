import { useRef, useState } from "react";
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

  const shapes = useStore((s) => s.shapes);
  const rev = useStore((s) => s.geometryRev);
  const viewMode = useStore((s) => s.viewMode);
  const activeLine = useStore((s) => s.activeLine);
  const presentation = useStore((s) => s.presentation);
  const physical = useStore((s) => s.physical);

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
      </div>
    </>
  );
}
