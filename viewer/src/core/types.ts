// The tessellation data contract (protocol v3) produced by the backend's
// ocp-tessellate pipeline. Mirrors frontend/src/lib/protocol.ts. The renderer
// consumes this tree directly — it is the single source of geometry truth.

/** [position, quaternionXYZW] — quaternion w is LAST (three.js order). */
export type Loc = [[number, number, number], [number, number, number, number]];

export interface BBox {
  xmin: number;
  xmax: number;
  ymin: number;
  ymax: number;
  zmin: number;
  zmax: number;
}

/** Flat-array geometry for one leaf. Arrays are plain JSON numbers (not typed). */
export interface LeafShape {
  vertices: number[]; // 3N xyz
  normals: number[]; // 3N, parallel to vertices
  triangles: number[]; // 3M indices into vertices, CCW
  edges: number[]; // flat segment-endpoint stream (6 floats per segment)
  obj_vertices?: number[]; // flat xyz of B-rep vertices
  triangles_per_face?: number[]; // count of triangles per OCP face (prefix-sum -> face ranges)
  segments_per_edge?: number[]; // count of segments per OCP edge
  face_types?: number[];
  edge_types?: number[];
}

export interface TessPart {
  id: string; // path "/Group/plate"
  name: string;
  type: string; // "shapes" | "edges" | "vertices"
  shape?: LeafShape; // present on leaves
  parts?: TessPart[]; // present on containers
  state?: [number, number]; // [facesVisible, edgesVisible]
  color?: string; // "#rrggbb"
  alpha?: number; // 0..1
  loc?: Loc;
  subtype?: string; // "solid" | "faces"
  renderback?: boolean;
  [k: string]: unknown;
}

export interface TessShapes {
  version: number;
  parts: TessPart[];
  loc: Loc | null;
  name: string;
  id: string; // e.g. "/Group"
  bb: BBox | null;
  [k: string]: unknown;
}

/** Visual knobs; the values mirror the legacy three-cad-viewer render presets. */
export interface RenderPreset {
  ambientIntensity: number;
  directIntensity: number;
  metalness: number;
  roughness: number;
  edgeColor: number | string;
  defaultOpacity: number;
  normalLen: number;
  /** IBL strength; technical subtle, presentation lush. */
  envIntensity?: number;
  aoEnabled?: boolean;
  shadowEnabled?: boolean;
  toneMapping?: boolean;
}

/** Camera construction defaults + optional restore state. */
export interface ViewerOptions {
  up?: "Z" | "Y";
  control?: "trackball" | "orbit";
  ortho?: boolean;
  position?: number[];
  quaternion?: number[];
  target?: number[];
  zoom?: number;
}

export type SelectKind = "face" | "edge" | "vertex" | "solid";

/** Resolved pick: emitted straight to the host (no name parsing needed). */
export interface PickEvent {
  kind: SelectKind;
  shapeId: string; // leaf id "/Group/plate"
  index: number; // OCP face/edge/vertex index, or 0 for whole solid
  /** |-delimited object name, for hosts that still parse it (highlight mode). */
  name: string;
  point?: [number, number, number];
  faceNormal?: [number, number, number];
}

/** Diff-style change payload (only changed keys present). */
export interface NotifyChange {
  position?: { new: number[] };
  quaternion?: { new: number[] };
  zoom?: { new: number };
  target?: { new: number[] };
  pick?: { new: PickEvent };
}
export type NotifyCallback = (change: NotifyChange) => void;

/** Per-leaf back-reference stored on each pickable object's userData. */
export interface PickMeta {
  shapeId: string;
  kind: SelectKind;
  trianglesPerFace?: number[];
  segmentsPerEdge?: number[];
  faceCenters?: Float32Array;
  faceOrdinal?: number; // position in the flat leaf list (highlight-mode line map)
  name: string; // |-delimited
}
