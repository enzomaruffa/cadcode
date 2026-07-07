import * as THREE from "three";
import RapierWorker from "./rapierWorker.ts?worker";
import type { SceneGraph } from "../core/SceneGraph";
import type { LeafObject } from "../core/leaf";
import { FLOATS_PER_BODY, type BodyInit, type InMsg, type OutMsg } from "./protocol";

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

  start(sg: SceneGraph, decompose = false): void {
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
      gravity: [0, 0, -diag * 6],
      groundZ: sg.bbox.min.z,
      extent: diag * 4,
      density: 1e-3,
      decompose,
    });
    this.active = true;
    this.dom.addEventListener("pointerdown", this.onDown);
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

/** Build the worker init for one leaf: decimated hull points, full mesh (for
 *  later decomposition), volume for mass, and the current pose. */
function bodyInitFor(geom: THREE.BufferGeometry, leaf: LeafObject): BodyInit | null {
  const pos = geom.getAttribute("position");
  if (!pos || pos.count < 4) return null;

  // Decimated hull points (constant screen-space cost regardless of mesh density).
  const step = Math.max(1, Math.floor(pos.count / MAX_HULL_PTS));
  const hull: number[] = [];
  for (let i = 0; i < pos.count; i += step) hull.push(pos.getX(i), pos.getY(i), pos.getZ(i));

  // Full verts + indices for convex decomposition (P1).
  const verts = new Float32Array(pos.array as ArrayLike<number>);
  const index = geom.getIndex();
  const indices = index
    ? new Uint32Array(index.array as ArrayLike<number>)
    : Uint32Array.from({ length: pos.count }, (_, i) => i);

  const p = leaf.group.position;
  const q = leaf.group.quaternion;
  return {
    points: new Float32Array(hull),
    verts,
    indices,
    pos: [p.x, p.y, p.z],
    quat: [q.x, q.y, q.z, q.w],
    volume: Math.max(meshVolume(pos, index), 1),
    ccd: true,
  };
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
