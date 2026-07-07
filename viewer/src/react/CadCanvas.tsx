import { forwardRef, useEffect, useImperativeHandle, useRef } from "react";
import { CadViewer, type InteractionMode } from "../core/CadViewer";
import { TECHNICAL, PRESENTATION } from "../materials/materials";
import type { SectionAxis } from "../interaction/Section";
import type { PhysicalData } from "../interaction/PhysicalOverlay";
import type { NotifyChange, PickEvent, RenderPreset, TessShapes, ViewerOptions } from "../core/types";
import type { RawJoint } from "../physics/protocol";

export type RenderProfile = "technical" | "presentation";
export type ViewMode = "technical" | "printability" | "highlight" | "geomdiff" | "physical" | "motion";

// Modes that recolor / re-tessellate the mesh — they force the flat technical
// preset so AO and tone mapping never distort the exact backend colors. Physical
// and motion annotate/animate on top of normal shading, so they're excluded.
const RECOLOR_MODES = new Set<ViewMode>(["printability", "highlight", "geomdiff"]);

export interface CadCanvasProps {
  shapes: TessShapes | null;
  geometryRev: number;
  renderProfile?: RenderProfile;
  viewMode?: ViewMode;
  activeLine?: number | null;
  highlight?: { faceLines?: number[] } | null;
  physical?: PhysicalData | null;
  physics?: boolean; // interactive gravity/drag playground
  joints?: RawJoint[]; // assembly joint graph → articulated physics constraints
  mode?: "select" | "section" | "measure";
  selectTopo?: "any" | "face" | "edge" | "vertex";
  interactive?: boolean;
  onPick?: (p: PickEvent) => void;
  onRevealLine?: (line: number) => void;
  className?: string;
}

export interface CadCanvasHandle {
  fitView: () => void;
  getCameraState: () => ViewerOptions;
  setCameraState: (c: ViewerOptions) => void;
  setSection: (axis: SectionAxis | null, offset?: number) => void;
  setMode: (mode: InteractionMode) => void;
  clearMeasure: () => void;
  setGrid: (on: boolean) => void;
  setPose: (poses: Record<string, [[number, number, number], [number, number, number, number]]>) => void;
  flashLeaves: (ids: string[], hex: string) => void;
  resetPhysics: () => void;
  setExplode: (factor: number) => void;
}

const presetFor = (p: RenderProfile | undefined): RenderPreset => (p === "presentation" ? PRESENTATION : TECHNICAL);

function sizeOf(el: HTMLElement) {
  const r = el.getBoundingClientRect();
  // Small floor so the renderer fits inside library grid cards, not just full panes.
  return { width: Math.max(Math.floor(r.width), 64), height: Math.max(Math.floor(r.height), 64) };
}

// Create-once / render-many React wrapper around CadViewer. Store-agnostic: it
// only emits callbacks, so both the main Viewport and the library PartPreview
// reuse it. Camera is preserved automatically by the long-lived CadViewer.
export const CadCanvas = forwardRef<CadCanvasHandle, CadCanvasProps>(function CadCanvas(props, ref) {
  const containerRef = useRef<HTMLDivElement>(null);
  const viewerRef = useRef<CadViewer | null>(null);
  // Latest callbacks without forcing the create-once effect to re-run.
  const cbRef = useRef<{ onPick?: (p: PickEvent) => void; onRevealLine?: (l: number) => void }>({});
  cbRef.current = { onPick: props.onPick, onRevealLine: props.onRevealLine };

  useImperativeHandle(ref, () => ({
    fitView: () => viewerRef.current?.fit(),
    getCameraState: () => viewerRef.current?.getCameraState() ?? {},
    setCameraState: (c) => viewerRef.current?.setCameraState(c),
    setSection: (axis, offset) => viewerRef.current?.setSection(axis, offset),
    setMode: (mode) => viewerRef.current?.setInteractionMode(mode),
    clearMeasure: () => viewerRef.current?.clearMeasure(),
    setGrid: (on) => viewerRef.current?.setGrid(on),
    setPose: (poses) => viewerRef.current?.setPose(poses),
    flashLeaves: (ids, hex) => viewerRef.current?.flashLeaves(ids, hex),
    resetPhysics: () => viewerRef.current?.resetPhysics(),
    setExplode: (factor) => viewerRef.current?.setExplode(factor),
  }));

  // Create the viewer once.
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const notify = (change: NotifyChange) => {
      if (change.pick) {
        const p = change.pick.new;
        cbRef.current.onPick?.(p);
      }
    };

    const viewer = new CadViewer(container, { up: "Z", control: "trackball", ortho: true }, notify);
    viewerRef.current = viewer;
    (window as unknown as { __viewer: CadViewer }).__viewer = viewer;

    let t: ReturnType<typeof setTimeout> | null = null;
    const ro = new ResizeObserver(() => {
      if (t) clearTimeout(t);
      t = setTimeout(() => {
        const s = sizeOf(container);
        viewer.resize(s.width, s.height);
      }, 80);
    });
    ro.observe(container);

    return () => {
      ro.disconnect();
      if (t) clearTimeout(t);
      viewer.dispose();
      viewerRef.current = null;
    };
  }, []);

  // Picking on/off.
  useEffect(() => {
    viewerRef.current?.setPicking(props.interactive !== false);
  }, [props.interactive]);

  // Selection topology (face / edge / vertex / any).
  useEffect(() => {
    viewerRef.current?.setSelectTopo(props.selectTopo ?? "face");
  }, [props.selectTopo]);

  // Re-render when geometry or look changes. Recolor view modes
  // (printability/highlight/geomdiff) force the flat preset so AO and tone
  // mapping never distort the exact backend colors; physical/motion keep the
  // normal render profile since they annotate/animate on top of it.
  useEffect(() => {
    const viewer = viewerRef.current;
    if (!viewer || !props.shapes || !props.shapes.parts?.length) return;
    const recolor = props.viewMode != null && RECOLOR_MODES.has(props.viewMode);
    viewer.render(props.shapes, recolor ? TECHNICAL : presetFor(props.renderProfile));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.geometryRev, props.renderProfile, props.viewMode, props.shapes]);

  // Physics playground on/off. Depends on geometryRev too so a re-render (which
  // stops the sim, since it invalidates the bodies) restarts it on the new model.
  useEffect(() => {
    const v = viewerRef.current;
    if (!v) return;
    if (props.physics) {
      v.setJoints(props.joints ?? []); // articulate from the current joint graph
      v.startPhysics();
    } else v.stopPhysics();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.physics, props.geometryRev]);

  // Highlight mode: glow the active line's faces in place (no rebuild).
  useEffect(() => {
    if (props.viewMode === "highlight") viewerRef.current?.recolorHighlight(props.activeLine ?? null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.activeLine, props.viewMode, props.geometryRev]);

  // Physical mode: draw the COM / support-polygon overlay (no rebuild). Cleared
  // whenever we leave the mode or the readout goes away.
  useEffect(() => {
    viewerRef.current?.setPhysical(props.viewMode === "physical" ? (props.physical ?? null) : null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.physical, props.viewMode, props.geometryRev]);

  return <div ref={containerRef} className={props.className ?? "viewport"} />;
});
