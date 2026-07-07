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

export interface InitMsg {
  type: "init";
  bodies: BodyInit[];
  gravity: [number, number, number];
  groundZ: number;
  extent: number; // ground half-extent (a big thin floor box)
  density: number; // mass per unit volume
}

export type InMsg =
  | InitMsg
  | { type: "grab"; index: number; pivot: [number, number, number] }
  | { type: "move"; point: [number, number, number] }
  | { type: "release" }
  | { type: "reset" }
  | { type: "stop" };

export interface FrameMsg {
  type: "frame";
  transforms: Float32Array; // 7 per body
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
