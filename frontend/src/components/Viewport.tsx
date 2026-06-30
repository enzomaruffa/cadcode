import { useEffect, useRef } from "react";
import { Display, Viewer } from "three-cad-viewer";
import "three-cad-viewer/dist/three-cad-viewer.css";
import { useStore } from "../lib/store";
import type { SelectKind } from "../lib/protocol";

const TREE_W = 220;

const KIND: Record<string, SelectKind> = { faces: "face", edges: "edge", vertices: "vertex" };

// three-cad-viewer names objects with "|" as the path delimiter
// (group.name = path.replaceAll("/", "|")). Shapes resolve as:
//   "|Group|plate"            -> whole solid           -> /Group/plate
//   "|Group|plate|faces_3"    -> face 3 of that solid  -> /Group/plate
//   "|Group|plate|faces|3"    -> (alt form) face 3
function parsePick(name: string): { shapeId: string; kind: SelectKind; index: number } | null {
  const segs = name.split("|").filter(Boolean);
  if (segs.length === 0) return null;

  const last = segs[segs.length - 1];
  const prev = segs.length >= 2 ? segs[segs.length - 2] : "";

  // "faces_3"
  const combined = /^(faces|edges|vertices)_(\d+)$/.exec(last);
  if (combined) {
    const shapeId = "/" + segs.slice(0, -1).join("/");
    return { shapeId, kind: KIND[combined[1]], index: parseInt(combined[2], 10) };
  }
  // "faces" "3"
  if (/^\d+$/.test(last) && KIND[prev]) {
    const shapeId = "/" + segs.slice(0, -2).join("/");
    return { shapeId, kind: KIND[prev], index: parseInt(last, 10) };
  }
  // whole solid
  return { shapeId: "/" + segs.join("/"), kind: "solid", index: 0 };
}

// Two render modes off the same data (plan §6): technical (flat, edges
// emphasized, for modeling) and presentation (PBR-ish, softer edges).
const TECHNICAL_RENDER = {
  ambientIntensity: 1.0,
  directIntensity: 1.1,
  metalness: 0.3,
  roughness: 0.65,
  edgeColor: 0x707070,
  defaultOpacity: 0.5,
  normalLen: 0,
};
const PRESENTATION_RENDER = {
  ambientIntensity: 1.3,
  directIntensity: 2.2,
  metalness: 0.55,
  roughness: 0.35,
  edgeColor: 0x2a2f37,
  defaultOpacity: 0.5,
  normalLen: 0,
};

interface CameraState {
  position?: number[];
  quaternion?: number[];
  zoom?: number;
  target?: number[];
}

function sizeOf(el: HTMLElement) {
  const r = el.getBoundingClientRect();
  return {
    width: Math.max(Math.floor(r.width), 480),
    height: Math.max(Math.floor(r.height), 320),
  };
}

export function Viewport() {
  const containerRef = useRef<HTMLDivElement>(null);
  const viewerRef = useRef<Viewer | null>(null);
  const cameraRef = useRef<CameraState>({});
  const renderedOnce = useRef(false);

  const shapes = useStore((s) => s.shapes);
  const rev = useStore((s) => s.geometryRev);
  const viewMode = useStore((s) => s.viewMode);
  const activeLine = useStore((s) => s.activeLine);
  const presentation = useStore((s) => s.presentation);

  // Create the Display + Viewer once.
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const { width, height } = sizeOf(container);

    const display = new Display(container, {
      // Glass mode floats the tree/tools as a collapsible overlay instead of a
      // fixed side block — declutters the viewport so geometry is the focus.
      cadWidth: width,
      height,
      treeWidth: TREE_W,
      theme: "dark",
      pinning: false,
      glass: true,
    });

    // Track the live camera so re-renders on every edit don't reset the view.
    const nc = (change: Record<string, unknown>) => {
      const cam = cameraRef.current;
      const field = (k: string): unknown => (change[k] as { new?: unknown } | undefined)?.new;
      const pos = field("position");
      const quat = field("quaternion");
      const zoom = field("zoom");
      const target = field("target");
      if (Array.isArray(pos)) cam.position = pos as number[];
      if (Array.isArray(quat)) cam.quaternion = quat as number[];
      if (typeof zoom === "number") cam.zoom = zoom;
      if (Array.isArray(target)) cam.target = target as number[];

      // Pick capture: when the select tool reports a selection change, read the
      // just-picked object. In highlight mode a face-part name encodes its source
      // line (geometry->code); otherwise resolve it to a selector server-side.
      if ("selected" in change || "selectedShapeIDs" in change) {
        const v = viewerRef.current as unknown as { lastObject?: { obj?: { name?: string } } } | null;
        const name = v?.lastObject?.obj?.name;
        if (name) {
          if (useStore.getState().viewMode === "highlight") {
            const m = /L(\d+)__/.exec(name);
            if (m) useStore.getState().setRevealLine(parseInt(m[1], 10));
          } else {
            const p = parsePick(name);
            if (p) useStore.getState().sendSelect(p.kind, p.shapeId, p.index);
          }
        }
      }
    };

    const viewer = new Viewer(display, { up: "Z", control: "trackball", ortho: true }, nc);
    viewerRef.current = viewer;
    (window as unknown as { __viewer: Viewer }).__viewer = viewer; // debug/E2E handle

    let roTimer: ReturnType<typeof setTimeout> | null = null;
    const ro = new ResizeObserver(() => {
      if (roTimer) clearTimeout(roTimer);
      roTimer = setTimeout(() => {
        const s = sizeOf(container);
        try {
          (viewer as unknown as { resizeCadView?: (a: number, b: number, c: number, d: boolean) => void }).resizeCadView?.(
            s.width,
            TREE_W,
            s.height,
            true, // glass
          );
        } catch {
          /* ignore resize failures */
        }
      }, 80);
    });
    ro.observe(container);

    return () => {
      ro.disconnect();
      try {
        (viewer as unknown as { dispose?: () => void }).dispose?.();
      } catch {
        /* ignore */
      }
      container.innerHTML = "";
      viewerRef.current = null;
      renderedOnce.current = false;
    };
  }, []);

  // Re-render whenever new geometry arrives.
  useEffect(() => {
    const viewer = viewerRef.current;
    if (!viewer || !shapes || !shapes.parts || shapes.parts.length === 0) return;

    const cam = cameraRef.current;
    const viewerOptions: Record<string, unknown> = { up: "Z", control: "trackball", ortho: true };
    if (renderedOnce.current && cam.position) {
      viewerOptions.position = cam.position;
      if (cam.quaternion) viewerOptions.quaternion = cam.quaternion;
      if (cam.target) viewerOptions.target = cam.target;
      if (typeof cam.zoom === "number") viewerOptions.zoom = cam.zoom;
    }

    // Highlight mode: recolor per-face parts — faces from the cursor's line glow
    // (code->geometry, plan §6). Part names are "L<line>__f<i>".
    if (viewMode === "highlight") {
      const recolor = (o: { parts?: unknown[]; name?: string; id?: string; color?: string }) => {
        if (Array.isArray(o.parts)) {
          o.parts.forEach((p) => recolor(p as typeof o));
        } else {
          const m = /L(\d+)__/.exec(o.name ?? o.id ?? "");
          const line = m ? parseInt(m[1], 10) : -1;
          o.color = activeLine != null && line === activeLine ? "#ffd23f" : "#3a4250";
        }
      };
      recolor(shapes as unknown as { parts?: unknown[] });
    }

    const vv = viewer as unknown as {
      setRaycastMode?: (f: boolean) => void;
      toggleAnimationLoop?: (f: boolean) => void;
      cadTools?: { enable?: (t: string) => void };
    };
    try {
      if (renderedOnce.current) {
        try {
          vv.setRaycastMode?.(false); // dispose the raycaster bound to the old scene
        } catch {
          /* ignore */
        }
        viewer.clear();
      }
      viewer.render(shapes, presentation ? PRESENTATION_RENDER : TECHNICAL_RENDER, viewerOptions);
      renderedOnce.current = true;

      // Enable click-to-select. The toolbar does exactly this trio: create the
      // raycaster (bound to the new scene), enable the select tool, and run the
      // animation loop so hover-raycast keeps `lastObject` current (plan §11 M2).
      try {
        vv.setRaycastMode?.(true);
        vv.cadTools?.enable?.("SelectObjects");
        vv.toggleAnimationLoop?.(true);
      } catch {
        /* select tool unavailable */
      }
    } catch (e) {
      console.error("viewer.render failed", e);
    }
  }, [rev, shapes, viewMode, activeLine, presentation]);

  return <div ref={containerRef} className="viewport" />;
}
