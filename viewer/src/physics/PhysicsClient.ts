import * as THREE from "three";
import { ConvexGeometry } from "three/examples/jsm/geometries/ConvexGeometry.js";
import RapierWorker from "./rapierWorker.ts?worker";
import type { SceneGraph } from "../core/SceneGraph";
import type { LeafObject } from "../core/leaf";
import { FLOATS_PER_BODY, type BodyInit, type InMsg, type JointInit, type OutMsg, type RawJoint } from "./protocol";

// Main-thread half of the physics playground. Extracts a collider + mass per
// shown leaf, hands them to the Rapier solver Web Worker, and applies the
// transforms the worker streams back — so the heavy simulation never touches
// the render thread. Grabbing raycasts here and drives a drag joint in the
// worker. Ephemeral: stopping restores the code-truth poses (the model is still
// the script; this is a sandbox).

const MAX_HULL_PTS = 256; // decimate dense meshes before shipping hull points

interface Tracked {
  leaf: LeafObject;
  init: { p: [number, number, number]; q: [number, number, number, number] };
}

export class PhysicsClient {
  active = false;
  private worker: Worker | null = null;
  private tracked: Tracked[] = [];
  private raycaster = new THREE.Raycaster();
  private ndc = new THREE.Vector2();
  private dragPlane = new THREE.Plane();
  private dragging = false;

  constructor(
    private dom: HTMLElement,
    private getCamera: () => THREE.Camera,
    private setOrbit: (enabled: boolean) => void,
    private requestDraw: () => void,
  ) {
    this.onDown = this.onDown.bind(this);
    this.onMove = this.onMove.bind(this);
    this.onUp = this.onUp.bind(this);
  }

  start(sg: SceneGraph, joints: RawJoint[] = []): void {
    this.stop();
    const size = new THREE.Vector3();
    sg.bbox.getSize(size);
    const diag = Math.max(size.length(), 1);

    const bodies: BodyInit[] = [];
    const tracked: Tracked[] = [];
    for (const leaf of sg.leaves) {
      const geom = leaf.front?.geometry as THREE.BufferGeometry | undefined;
      if (!geom) continue;
      const body = bodyInitFor(geom, leaf);
      if (!body) continue;
      bodies.push(body);
      tracked.push({ leaf, init: { p: body.pos, q: body.quat } });
    }
    if (!bodies.length) return;

    this.tracked = tracked;
    this.worker = new RapierWorker();
    this.worker.onmessage = (e: MessageEvent<OutMsg>) => this.onMessage(e.data);
    this.worker.onerror = (e) => console.error("[physics] worker error:", e.message, e.filename, e.lineno);
    this.worker.onmessageerror = (e) => console.error("[physics] worker message error:", e);
    this.post({
      type: "init",
      bodies,
      joints: this.resolveJoints(joints, tracked),
      gravity: [0, 0, -diag * 6],
      groundZ: sg.bbox.min.z,
      extent: diag * 4,
      density: 1e-3,
    });
    this.active = true;
    this.dom.addEventListener("pointerdown", this.onDown);
  }

  /** Resolve backend joints (part names + local axes) to worker joints (body
   *  indices + the DOF axis expressed in body A's local frame, so a hinge spins
   *  about its true world axis regardless of how the two parts are oriented). */
  private resolveJoints(joints: RawJoint[], tracked: Tracked[]): JointInit[] {
    const idx = (name: string) => tracked.findIndex((t) => t.leaf.group.name.split("|").pop() === name);
    const out: JointInit[] = [];
    const worldAxis = new THREE.Vector3();
    const invA = new THREE.Quaternion();
    for (const j of joints) {
      const a = idx(j.a);
      const b = idx(j.b);
      if (a < 0 || b < 0) continue;
      // hinge/slide axis: B-local → world → A-local
      worldAxis.set(j.axisB[0], j.axisB[1], j.axisB[2]).applyQuaternion(tracked[b].leaf.group.quaternion).normalize();
      invA.copy(tracked[a].leaf.group.quaternion).invert();
      worldAxis.applyQuaternion(invA);
      out.push({
        kind: j.kind,
        a,
        b,
        anchorA: j.anchorA,
        anchorB: j.anchorB,
        axis: [worldAxis.x, worldAxis.y, worldAxis.z],
        range: j.range,
      });
    }
    return out;
  }

  private post(m: InMsg): void {
    this.worker?.postMessage(m);
  }

  private onMessage(m: OutMsg): void {
    if (m.type === "error") {
      console.error("[physics] solver:", m.error);
      return;
    }
    if (m.type !== "frame") return; // "ready" — nothing to do
    const t = m.transforms;
    const n = Math.min(this.tracked.length, Math.floor(t.length / FLOATS_PER_BODY));
    for (let i = 0; i < n; i++) {
      const o = i * FLOATS_PER_BODY;
      this.tracked[i].leaf.group.position.set(t[o], t[o + 1], t[o + 2]);
      this.tracked[i].leaf.group.quaternion.set(t[o + 3], t[o + 4], t[o + 5], t[o + 6]);
    }
    this.requestDraw();
  }

  /** Drop every part back to its code-truth pose, at rest. */
  reset(): void {
    this.post({ type: "reset" });
  }

  stop(): void {
    this.endDrag();
    this.dom.removeEventListener("pointerdown", this.onDown);
    // Restore the code-truth poses so the model is exactly the script again.
    for (const { leaf, init } of this.tracked) {
      leaf.group.position.set(init.p[0], init.p[1], init.p[2]);
      leaf.group.quaternion.set(init.q[0], init.q[1], init.q[2], init.q[3]);
    }
    if (this.worker) {
      this.worker.onmessage = null;
      this.worker.terminate();
      this.worker = null;
    }
    this.tracked = [];
    this.active = false;
    this.requestDraw();
  }

  dispose(): void {
    this.stop();
  }

  // --- dragging -------------------------------------------------------------

  private setNdc(ev: PointerEvent): void {
    const r = this.dom.getBoundingClientRect();
    this.ndc.set(((ev.clientX - r.left) / r.width) * 2 - 1, -((ev.clientY - r.top) / r.height) * 2 + 1);
  }

  private onDown(ev: PointerEvent): void {
    if (!this.worker) return;
    this.setNdc(ev);
    this.raycaster.setFromCamera(this.ndc, this.getCamera());
    const meshes = this.tracked.map((t) => t.leaf.front).filter((m): m is THREE.Mesh => !!m);
    const hits = this.raycaster.intersectObjects(meshes, false);
    if (!hits.length) return; // empty space → let OrbitControls handle it
    const hit = hits[0];
    const index = this.tracked.findIndex((t) => t.leaf.front === hit.object);
    if (index < 0) return;
    ev.preventDefault();
    this.setOrbit(false);
    this.dragging = true;

    const point = hit.point.clone();
    const n = new THREE.Vector3();
    this.getCamera().getWorldDirection(n);
    this.dragPlane.setFromNormalAndCoplanarPoint(n, point);

    // Pivot in the body's local frame (current live pose from the last frame).
    const g = this.tracked[index].leaf.group;
    const local = g.worldToLocal(point.clone());
    this.post({ type: "grab", index, pivot: [local.x, local.y, local.z] });
    this.post({ type: "move", point: [point.x, point.y, point.z] });

    window.addEventListener("pointermove", this.onMove);
    window.addEventListener("pointerup", this.onUp);
  }

  private onMove(ev: PointerEvent): void {
    if (!this.dragging) return;
    this.setNdc(ev);
    this.raycaster.setFromCamera(this.ndc, this.getCamera());
    const target = new THREE.Vector3();
    if (this.raycaster.ray.intersectPlane(this.dragPlane, target)) {
      this.post({ type: "move", point: [target.x, target.y, target.z] });
    }
  }

  private onUp(): void {
    this.endDrag();
  }

  private endDrag(): void {
    if (!this.dragging) return;
    this.dragging = false;
    window.removeEventListener("pointermove", this.onMove);
    window.removeEventListener("pointerup", this.onUp);
    this.post({ type: "release" });
    this.setOrbit(true);
  }
}

const VOXEL_RES = 16; // grid cells along the longest axis for concave parts
// mesh volume ÷ convex-hull volume below this ⇒ concave (a bowl scores low; a
// sphere/box scores ~1, so convex parts keep their smooth, cheap hull collider).
const CONCAVE_RATIO = 0.85;

/** Build the worker init for one leaf: a convex hull for convex parts, or a
 *  solid voxelization for concave ones (so a hollow/perforated part collides on
 *  its real surface, not its filled hull). Plus volume for mass + current pose. */
function bodyInitFor(geom: THREE.BufferGeometry, leaf: LeafObject): BodyInit | null {
  const pos = geom.getAttribute("position");
  if (!pos || pos.count < 4) return null;

  // Decimated hull points (constant cost regardless of mesh density).
  const step = Math.max(1, Math.floor(pos.count / MAX_HULL_PTS));
  const hull: number[] = [];
  for (let i = 0; i < pos.count; i += step) hull.push(pos.getX(i), pos.getY(i), pos.getZ(i));

  const index = geom.getIndex();
  const vol = Math.max(meshVolume(pos, index), 1);
  geom.computeBoundingBox();

  // Concave (hollow / perforated) → voxelize so the collider follows the real
  // surface; a convex hull would fill the cavity and collide wrong. Detect via
  // mesh volume vs convex-hull volume (robust: a sphere ~1, a bowl ≪ 1).
  let voxels: Float32Array | null = null;
  let voxelSize = 0;
  if (vol / hullVolume(hull, vol) < CONCAVE_RATIO) {
    const vx = voxelizeLocal(geom);
    if (vx && vx.centers.length >= 3) {
      voxels = vx.centers;
      voxelSize = vx.size;
    }
  }

  const p = leaf.group.position;
  const q = leaf.group.quaternion;
  return {
    points: new Float32Array(hull),
    voxels,
    voxelSize,
    pos: [p.x, p.y, p.z],
    quat: [q.x, q.y, q.z, q.w],
    volume: vol,
    ccd: true,
  };
}

/** Surface voxelization in geometry-local space: sample every triangle at ~voxel
 *  spacing and mark the voxel each sample lands in. A shell of voxels is exactly
 *  right for the hollow/perforated parts we voxelize (a ball rests on the real
 *  surface, not a filled hull) and is robust — no raycasting. Returns occupied
 *  voxel CENTERS (flat xyz) + the cubic voxel edge. */
function voxelizeLocal(geom: THREE.BufferGeometry): { centers: Float32Array; size: number } | null {
  const bb = geom.boundingBox!;
  const sx = bb.max.x - bb.min.x;
  const sy = bb.max.y - bb.min.y;
  const sz = bb.max.z - bb.min.z;
  const vs = Math.max(Math.max(sx, sy, sz) / VOXEL_RES, 1e-4);

  const pos = geom.getAttribute("position");
  const index = geom.getIndex();
  const nTri = index ? index.count : pos.count;
  const a = new THREE.Vector3();
  const b = new THREE.Vector3();
  const c = new THREE.Vector3();
  const p = new THREE.Vector3();
  const occupied = new Set<number>();
  const nx = Math.max(1, Math.ceil(sx / vs));
  const ny = Math.max(1, Math.ceil(sy / vs));
  const key = (x: number, y: number, z: number) => {
    const i = Math.min(nx - 1, Math.max(0, Math.floor((x - bb.min.x) / vs)));
    const j = Math.min(ny - 1, Math.max(0, Math.floor((y - bb.min.y) / vs)));
    const k = Math.max(0, Math.floor((z - bb.min.z) / vs));
    return (k * ny + j) * nx + i;
  };
  for (let t = 0; t < nTri; t += 3) {
    const ia = index ? index.getX(t) : t;
    const ib = index ? index.getX(t + 1) : t + 1;
    const ic = index ? index.getX(t + 2) : t + 2;
    a.fromBufferAttribute(pos, ia);
    b.fromBufferAttribute(pos, ib);
    c.fromBufferAttribute(pos, ic);
    const steps = Math.max(1, Math.ceil(Math.max(a.distanceTo(b), a.distanceTo(c)) / vs));
    for (let u = 0; u <= steps; u++) {
      for (let v = 0; v <= steps - u; v++) {
        const bu = u / steps;
        const bv = v / steps;
        p.set(
          a.x + bu * (b.x - a.x) + bv * (c.x - a.x),
          a.y + bu * (b.y - a.y) + bv * (c.y - a.y),
          a.z + bu * (b.z - a.z) + bv * (c.z - a.z),
        );
        occupied.add(key(p.x, p.y, p.z));
      }
    }
  }
  const centers: number[] = [];
  for (const cell of occupied) {
    const i = cell % nx;
    const j = Math.floor(cell / nx) % ny;
    const k = Math.floor(cell / (nx * ny));
    centers.push(bb.min.x + (i + 0.5) * vs, bb.min.y + (j + 0.5) * vs, bb.min.z + (k + 0.5) * vs);
  }
  return centers.length ? { centers: new Float32Array(centers), size: vs } : null;
}

/** Volume of the convex hull of flat xyz points (≥ the mesh volume it wraps). */
function hullVolume(points: number[], fallback: number): number {
  try {
    const pts: THREE.Vector3[] = [];
    for (let i = 0; i + 2 < points.length; i += 3) pts.push(new THREE.Vector3(points[i], points[i + 1], points[i + 2]));
    if (pts.length < 4) return fallback;
    const g = new ConvexGeometry(pts);
    const v = meshVolume(g.getAttribute("position") as THREE.BufferAttribute, null);
    g.dispose();
    return Math.max(v, fallback); // hull encloses the mesh → ratio stays ≤ 1
  } catch {
    return fallback;
  }
}

/** Signed volume of a closed triangle mesh (Σ v0·(v1×v2)/6). */
function meshVolume(pos: THREE.BufferAttribute | THREE.InterleavedBufferAttribute, index: THREE.BufferAttribute | null): number {
  let v = 0;
  const a = new THREE.Vector3();
  const b = new THREE.Vector3();
  const c = new THREE.Vector3();
  const tris = index ? index.count : pos.count;
  for (let i = 0; i < tris; i += 3) {
    const ia = index ? index.getX(i) : i;
    const ib = index ? index.getX(i + 1) : i + 1;
    const ic = index ? index.getX(i + 2) : i + 2;
    a.fromBufferAttribute(pos, ia);
    b.fromBufferAttribute(pos, ib);
    c.fromBufferAttribute(pos, ic);
    v += a.dot(b.clone().cross(c));
  }
  return Math.abs(v) / 6;
}
