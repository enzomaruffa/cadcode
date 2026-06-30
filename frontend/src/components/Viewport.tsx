import { useRef } from "react";
import { CadCanvas, type CadCanvasHandle, type PickEvent } from "@cadcode/viewer";
import { useStore } from "../lib/store";

// The viewport is now our own renderer (@cadcode/viewer). The store contract is
// unchanged: a pick becomes sendSelect(kind, shapeId, index); in highlight mode
// a pick reveals the source line instead.
export function Viewport() {
  const viewerRef = useRef<CadCanvasHandle>(null);

  const shapes = useStore((s) => s.shapes);
  const rev = useStore((s) => s.geometryRev);
  const viewMode = useStore((s) => s.viewMode);
  const activeLine = useStore((s) => s.activeLine);
  const presentation = useStore((s) => s.presentation);

  const onPick = (p: PickEvent) => {
    const st = useStore.getState();
    if (st.viewMode === "highlight") {
      const m = /L(\d+)__/.exec(p.name);
      if (m) st.setRevealLine(parseInt(m[1], 10));
      return;
    }
    st.sendSelect(p.kind, p.shapeId, p.index);
  };

  return (
    <CadCanvas
      ref={viewerRef}
      className="viewport"
      shapes={shapes}
      geometryRev={rev}
      renderProfile={presentation ? "presentation" : "technical"}
      viewMode={viewMode}
      activeLine={activeLine}
      onPick={onPick}
    />
  );
}
