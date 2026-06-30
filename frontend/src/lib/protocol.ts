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
export const ACCEPT_PATCH = "accept_patch";
export const REJECT_PATCH = "reject_patch";

// backend -> frontend
export const GEOMETRY = "geometry";
export const ERROR = "error";
export const STATUS = "status";
export const MEASUREMENT = "measurement";
export const AGENT_MESSAGE = "agent_message";
export const AGENT_PATCH = "agent_patch";

export interface AgentMessagePayload {
  role: "assistant" | "user";
  text: string;
  error?: boolean;
}

export interface AgentPatchPayload {
  diff: string;
  rationale: string;
  targets: string[];
  new_source: string;
}

export interface Spec {
  passed: boolean;
  message: string;
}

export interface Param {
  name: string;
  value: number;
  line: number;
  is_int: boolean;
}

export interface GeometryPayload {
  shapes: TessShapes;
  states: Record<string, number[]>;
  bbox: BBox | null;
  ops: unknown[];
  specs: Spec[];
  params: Param[];
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

export type SelectKind = "face" | "edge" | "vertex" | "solid";

// Backend MEASUREMENT payload (app/kernel/select.py).
export interface MeasurementPayload {
  kind: SelectKind;
  index: number;
  selector?: string;
  selector_confidence?: "high" | "medium" | "low" | "n/a";
  description?: string;
  properties?: Record<string, unknown>;
  solid?: { bbox?: { min: number[]; max: number[]; size: number[] }; volume?: number };
  error?: string;
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
