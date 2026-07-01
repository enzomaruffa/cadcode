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
export const SET_MODE = "set_mode";
export const CHECKPOINT = "checkpoint";
export const ROLLBACK = "rollback";
export const UNDO = "undo";
export const REDO = "redo";
export const PREVIEW_DIFF = "preview_diff";

// backend -> frontend
export const GEOMETRY = "geometry";
export const ERROR = "error";
export const STATUS = "status";
export const MEASUREMENT = "measurement";
export const AGENT_MESSAGE = "agent_message";
export const AGENT_PATCH = "agent_patch";
export const HISTORY = "history";
export const SOURCE = "source";

export interface Commit {
  sha: string;
  short: string;
  message: string;
  time: number;
}

export interface HistoryPayload {
  commits: Commit[];
  can_undo: boolean;
  can_redo: boolean;
}

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
  min?: number;
  max?: number;
  step?: number;
}

// Physical-properties readout (backend app/kernel/massprops.py).
export interface PhysicalPayload {
  mass_g: number;
  volume_cm3: number;
  area_mm2: number;
  com: [number, number, number];
  inertia: number[][];
  principal: number[];
  principal_axes: number[][];
  base_z: number;
  com_height: number;
  footprint: [number, number][];
  tip_angle: number | null;
  stable: boolean;
  floats: boolean;
  effective_density: number;
  filament_g: number;
  filament_len_mm: number | null;
  cost: number;
  print_time_min: number;
  material_name: string;
  parts: number;
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
  mode?: "technical" | "printability" | "highlight" | "geomdiff" | "physical";
  print_stats?: Record<string, number | string> | null;
  physical?: PhysicalPayload | null;
}

export type ViewMode = "technical" | "printability" | "highlight" | "physical";

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

// The tessellation tree types are owned by the renderer package (single source
// of truth for geometry). Import for local use here and re-export so the WS
// protocol and the viewer agree on one definition.
import type { BBox, TessShapes, TessPart } from "@cadcode/viewer";
export type { BBox, TessShapes, TessPart };

let counter = 0;
export function makeId(): string {
  counter += 1;
  return `c${Date.now().toString(36)}-${counter}`;
}
