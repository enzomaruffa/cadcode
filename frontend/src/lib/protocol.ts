// Mirrors backend app/protocol.py — the one JSON envelope (plan §8).

export interface Envelope<T = Record<string, unknown>> {
  type: string;
  id: string;
  payload: T;
}

// frontend -> backend
export const EDIT = "edit";
export const RUN = "run";
export const SELECT = "select";
export const CHAT = "chat";

// backend -> frontend
export const GEOMETRY = "geometry";
export const ERROR = "error";
export const STATUS = "status";
export const MEASUREMENT = "measurement";

export interface GeometryPayload {
  shapes: TessShapes;
  states: Record<string, number[]>;
  bbox: BBox | null;
  ops: unknown[];
  stdout: string;
  stale: boolean;
}

export interface ErrorPayload {
  message: string;
  traceback: string;
  line: number | null;
}

export interface StatusPayload {
  state: "idle" | "running" | "ok" | "error";
  detail: string;
}

export interface BBox {
  xmin: number;
  xmax: number;
  ymin: number;
  ymax: number;
  zmin: number;
  zmax: number;
}

// The three-cad-viewer shape tree (states embedded per-part as `state`).
export interface TessShapes {
  version: number;
  parts: TessPart[];
  loc: unknown;
  name: string;
  id: string;
  bb: BBox | null;
  [k: string]: unknown;
}

export interface TessPart {
  id: string;
  name: string;
  type: string;
  shape?: Record<string, unknown>;
  parts?: TessPart[];
  state?: number[];
  color?: string;
  [k: string]: unknown;
}

let counter = 0;
export function makeId(): string {
  counter += 1;
  return `c${Date.now().toString(36)}-${counter}`;
}
