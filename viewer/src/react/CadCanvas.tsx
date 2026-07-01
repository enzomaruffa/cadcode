import { forwardRef, useEffect, useImperativeHandle, useRef } from "react";
import { CadViewer, type InteractionMode } from "../core/CadViewer";
import { TECHNICAL, PRESENTATION } from "../materials/materials";
import type { SectionAxis } from "../interaction/Section";
import type { NotifyChange, PickEvent, RenderPreset, TessShapes, ViewerOptions } from "../core/types";

export type RenderProfile = "technical" | "presentation";
export type ViewMode = "technical" | "printability" | "highlight" | "geomdiff";

export interface CadCanvasProps {
  shapes: TessShapes | null;
  geometryRev: number;
  renderProfile?: RenderProfile;
  viewMode?: ViewMode;
  activeLine?: number | null;
  highlight?: { faceLines?: number[] } | null;
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
}

const presetFor = (p: RenderProfile | undefined): RenderPreset => (p === "presentation" ? PRESENTATION : TECHNICAL);

function sizeOf(el: HTMLElement) {
  const r = el.getBoundingClientRect();
  return { width: Math.max(Math.floor(r.width), 320), height: Math.max(Math.floor(r.height), 240) };
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

  // Re-render when geometry or look changes. Diagnostic view modes
  // (printability/highlight/geomdiff) force the flat preset so AO and tone
  // mapping never distort the exact backend colors.
  useEffect(() => {
    const viewer = viewerRef.current;
    if (!viewer || !props.shapes || !props.shapes.parts?.length) return;
    const diagnostic = props.viewMode != null && props.viewMode !== "technical";
    viewer.render(props.shapes, diagnostic ? TECHNICAL : presetFor(props.renderProfile));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.geometryRev, props.renderProfile, props.viewMode, props.shapes]);

  // Highlight mode: glow the active line's faces in place (no rebuild).
  useEffect(() => {
    if (props.viewMode === "highlight") viewerRef.current?.recolorHighlight(props.activeLine ?? null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.activeLine, props.viewMode, props.geometryRev]);

  return <div ref={containerRef} className={props.className ?? "viewport"} />;
});
