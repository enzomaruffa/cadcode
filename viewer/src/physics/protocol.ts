// Message protocol between the main thread (PhysicsClient) and the Rapier
// solver Web Worker. Transforms flow back as a flat Float32Array — 7 floats per
// body (x,y,z, qx,qy,qz,qw) — cheap to structured-clone at 60Hz.

export interface BodyInit {
  points: Float32Array; // flat xyz convex-hull points (convex parts)
  voxels: Float32Array | null; // flat xyz occupied-voxel centers (concave parts) or null
  voxelSize: number; // cubic voxel edge (when voxels != null)
  pos: [number, number, number];
  quat: [number, number, number, number];
  volume: number; // mesh volume (drives mass = volume × density)
  ccd: boolean;
}

// A constraint as it comes from the backend joint graph (part names + local axes).
export interface RawJoint {
  kind: "revolute" | "prismatic" | "spherical" | "cylindrical" | "fixed";
  a: string; // show name of part A
  b: string; // show name of part B
  anchorA: [number, number, number];
  anchorB: [number, number, number];
  axisA: [number, number, number];
  axisB: [number, number, number];
  range: [number, number] | null; // degrees (revolute) or mm (prismatic)
}

// A constraint resolved to body indices + a single DOF axis in body A's frame.
export interface JointInit {
  kind: RawJoint["kind"];
  a: number;
  b: number;
  anchorA: [number, number, number];
  anchorB: [number, number, number];
  axis: [number, number, number];
  range: [number, number] | null;
}

export interface InitMsg {
  type: "init";
  bodies: BodyInit[];
  joints: JointInit[];
  gravity: [number, number, number];
  groundZ: number;
  extent: number; // ground half-extent (a big thin floor box)
  density: number; // mass per unit volume
}

// Drive every ranged revolute/prismatic joint through its declared range as a
// time-based triangle wave (Rapier 0.19 has no joint-angle getter, so the
// target is derived from the clock, not from feedback). "off" restores free
// articulation. Phase-5 seam: an eventual particle tier extends InitMsg with
// emitters and FrameMsg with a `particles: Float32Array` — same worker, second
// fixed-rate loop against the already-shipped voxel colliders.
export interface DriveMsg {
  type: "drive";
  mode: "range-pingpong" | "off";
  period_s?: number; // full down-up-down cycle, default 4s
}

export type InMsg =
  | InitMsg
  | DriveMsg
  | { type: "grab"; index: number; pivot: [number, number, number] }
  | { type: "move"; point: [number, number, number] }
  | { type: "release" }
  | { type: "reset" }
  | { type: "stop" };

export interface FrameMsg {
  type: "frame";
  transforms: Float32Array; // 7 per body
  // Body-index pairs currently in contact (part↔part only, jointed pairs and the
  // ground excluded). Attached only on frames where the active set CHANGED.
  contacts?: Int32Array;
}
export interface ReadyMsg {
  type: "ready";
  bodies: number;
}
export interface ErrorMsg {
  type: "error";
  error: string;
}
export type OutMsg = FrameMsg | ReadyMsg | ErrorMsg;

export const FLOATS_PER_BODY = 7;
