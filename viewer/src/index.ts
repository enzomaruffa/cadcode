// @cadcode/viewer — cadcode's own three.js renderer for OCP tessellation.
export { CadViewer, type InteractionMode } from "./core/CadViewer";
export type { SectionAxis } from "./interaction/Section";
export type { PhysicalData } from "./interaction/PhysicalOverlay";
export { CadCanvas } from "./react/CadCanvas";
export type { CadCanvasProps, CadCanvasHandle, RenderProfile, ViewMode } from "./react/CadCanvas";
export type { RawJoint } from "./physics/protocol";
export { TECHNICAL, PRESENTATION, PREVIEW, DEFAULT_COLOR } from "./materials/materials";
export { applyHighlight, applyFaceColors, PROVENANCE_PALETTE, HIGHLIGHT_GLOW } from "./materials/colorApi";
export type {
  TessShapes,
  TessPart,
  LeafShape,
  Loc,
  BBox,
  RenderPreset,
  ViewerOptions,
  SelectKind,
  PickEvent,
  NotifyChange,
  NotifyCallback,
  PickMeta,
} from "./core/types";
