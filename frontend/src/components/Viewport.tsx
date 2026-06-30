import { useEffect, useRef } from "react";
import { Display, Viewer } from "three-cad-viewer";
import "three-cad-viewer/dist/three-cad-viewer.css";
import { useStore } from "../lib/store";

const TREE_W = 220;

// PBR-ish lighting tuned for technical modeling (plan §6: technical render mode).
const renderOptions = {
  ambientIntensity: 1.0,
  directIntensity: 1.1,
  metalness: 0.3,
  roughness: 0.65,
  edgeColor: 0x707070,
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

  // Create the Display + Viewer once.
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const { width, height } = sizeOf(container);

    const display = new Display(container, {
      cadWidth: Math.max(width - TREE_W, 360),
      height,
      treeWidth: TREE_W,
      theme: "dark",
      pinning: false,
      glass: false,
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
    };

    const viewer = new Viewer(display, { up: "Z", control: "trackball", ortho: true }, nc);
    viewerRef.current = viewer;

    const ro = new ResizeObserver(() => {
      const s = sizeOf(container);
      try {
        (viewer as unknown as { resizeCadView?: (a: number, b: number, c: number, d: boolean) => void }).resizeCadView?.(
          Math.max(s.width - TREE_W, 360),
          TREE_W,
          s.height,
          false,
        );
      } catch {
        /* ignore resize failures */
      }
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

    try {
      if (renderedOnce.current) viewer.clear();
      viewer.render(shapes, renderOptions, viewerOptions);
      renderedOnce.current = true;
    } catch (e) {
      console.error("viewer.render failed", e);
    }
  }, [rev, shapes]);

  return <div ref={containerRef} className="viewport" />;
}
