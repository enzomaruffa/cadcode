// @cadcode/viewer — cadcode's own three.js renderer for OCP tessellation.
export { CadViewer } from "./core/CadViewer";
export { CadCanvas } from "./react/CadCanvas";
export type { CadCanvasProps, CadCanvasHandle, RenderProfile, ViewMode } from "./react/CadCanvas";
export { TECHNICAL, PRESENTATION, PREVIEW, DEFAULT_COLOR } from "./materials/materials";
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
