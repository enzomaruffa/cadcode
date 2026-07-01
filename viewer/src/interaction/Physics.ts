import * as THREE from "three";
import * as CANNON from "cannon-es";
import { ConvexGeometry } from "three/examples/jsm/geometries/ConvexGeometry.js";
import type { SceneGraph } from "../core/SceneGraph";
import type { LeafObject } from "../core/leaf";

// A real-time rigid-body playground: each shown part becomes a dynamic body
// (convex-hull collider, box fallback), gravity pulls them onto a ground plane,
// and you can grab + drag one — a constraint pulls it toward the pointer so it
// pushes (and is blocked by) the others. Ephemeral: stopping restores the
// code-truth poses (the model is still the script; this is just a sandbox).

const MAX_HULL_PTS = 400; // decimate dense meshes before hull-building (perf)

interface PBody {
  leaf: LeafObject;
  body: CANNON.Body;
  init: { p: [number, number, number]; q: [number, number, number, number] };
}

export class Physics {
  active = false;
  private world: CANNON.World | null = null;
  private bodies: PBody[] = [];
  private jointBody: CANNON.Body | null = null;
  private constraint: CANNON.PointToPointConstraint | null = null;
  private raycaster = new THREE.Raycaster();
  private ndc = new THREE.Vector2();
  private dragPlane = new THREE.Plane();
  private dragging = false;

  constructor(
    private dom: HTMLElement,
    private getCamera: () => THREE.Camera,
    private setOrbit: (enabled: boolean) => void,
  ) {
    this.onDown = this.onDown.bind(this);
    this.onMove = this.onMove.bind(this);
    this.onUp = this.onUp.bind(this);
  }

  start(sg: SceneGraph): void {
    this.stop();
    const size = new THREE.Vector3();
    sg.bbox.getSize(size);
    const diag = Math.max(size.length(), 1);

    const world = new CANNON.World({ gravity: new CANNON.Vec3(0, 0, -diag * 6) });
    world.broadphase = new CANNON.SAPBroadphase(world);
    world.allowSleep = true;
    world.defaultContactMaterial.friction = 0.4;
    world.defaultContactMaterial.restitution = 0.05;

    // Floor at the model's lowest point (Z-up), so parts settle where they sit.
    const ground = new CANNON.Body({ type: CANNON.Body.STATIC, shape: new CANNON.Plane() });
    ground.position.set(0, 0, sg.bbox.min.z);
    world.addBody(ground);

    // The point the drag constraint pulls a grabbed body toward (moved by hand).
    const joint = new CANNON.Body({
      type: CANNON.Body.KINEMATIC,
      collisionFilterGroup: 0,
      collisionFilterMask: 0,
      shape: new CANNON.Sphere(0.1),
    });
    world.addBody(joint);
    this.jointBody = joint;

    const bodies: PBody[] = [];
    for (const leaf of sg.leaves) {
      const geom = leaf.front?.geometry as THREE.BufferGeometry | undefined;
      if (!geom) continue;
      const { shape, offset } = colliderFor(geom);
      const body = new CANNON.Body({ mass: Math.max(bboxVolume(geom) * 1e-3, 0.2) });
      body.addShape(shape, offset);
      const p = leaf.group.position;
      const q = leaf.group.quaternion;
      body.position.set(p.x, p.y, p.z);
      body.quaternion.set(q.x, q.y, q.z, q.w);
      body.linearDamping = 0.1;
      body.angularDamping = 0.25;
      world.addBody(body);
      bodies.push({ leaf, body, init: { p: [p.x, p.y, p.z], q: [q.x, q.y, q.z, q.w] } });
    }

    this.world = world;
    this.bodies = bodies;
    this.active = true;
    this.dom.addEventListener("pointerdown", this.onDown);
  }

  /** Advance the sim and copy body transforms back onto the leaf groups. */
  step(dt: number): void {
    if (!this.world) return;
    this.world.step(1 / 60, dt, 3);
    for (const { leaf, body } of this.bodies) {
      leaf.group.position.set(body.position.x, body.position.y, body.position.z);
      leaf.group.quaternion.set(body.quaternion.x, body.quaternion.y, body.quaternion.z, body.quaternion.w);
    }
  }

  /** Drop every part back to its code-truth pose, at rest. */
  reset(): void {
    for (const { body, init } of this.bodies) {
      body.position.set(init.p[0], init.p[1], init.p[2]);
      body.quaternion.set(init.q[0], init.q[1], init.q[2], init.q[3]);
      body.velocity.setZero();
      body.angularVelocity.setZero();
      body.wakeUp();
    }
  }

  stop(): void {
    this.endDrag();
    this.dom.removeEventListener("pointerdown", this.onDown);
    // Restore the code-truth poses so the model is exactly the script again.
    for (const { leaf, init } of this.bodies) {
      leaf.group.position.set(init.p[0], init.p[1], init.p[2]);
      leaf.group.quaternion.set(init.q[0], init.q[1], init.q[2], init.q[3]);
    }
    this.world = null;
    this.bodies = [];
    this.jointBody = null;
    this.active = false;
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
    if (!this.world || !this.jointBody) return;
    this.setNdc(ev);
    this.raycaster.setFromCamera(this.ndc, this.getCamera());
    const meshes = this.bodies.map((b) => b.leaf.front).filter((m): m is THREE.Mesh => !!m);
    const hits = this.raycaster.intersectObjects(meshes, false);
    if (!hits.length) return; // empty space → let OrbitControls handle it
    const hit = hits[0];
    const pb = this.bodies.find((b) => b.leaf.front === hit.object);
    if (!pb) return;
    ev.preventDefault();
    this.setOrbit(false);
    this.dragging = true;

    const point = hit.point.clone();
    const n = new THREE.Vector3();
    this.getCamera().getWorldDirection(n);
    this.dragPlane.setFromNormalAndCoplanarPoint(n, point);

    this.jointBody.position.set(point.x, point.y, point.z);
    const worldPivot = new CANNON.Vec3(point.x - pb.body.position.x, point.y - pb.body.position.y, point.z - pb.body.position.z);
    const localPivot = pb.body.quaternion.inverse().vmult(worldPivot);
    this.constraint = new CANNON.PointToPointConstraint(pb.body, localPivot, this.jointBody, new CANNON.Vec3());
    this.world.addConstraint(this.constraint);
    pb.body.wakeUp();

    window.addEventListener("pointermove", this.onMove);
    window.addEventListener("pointerup", this.onUp);
  }

  private onMove(ev: PointerEvent): void {
    if (!this.dragging || !this.jointBody) return;
    this.setNdc(ev);
    this.raycaster.setFromCamera(this.ndc, this.getCamera());
    const target = new THREE.Vector3();
    if (this.raycaster.ray.intersectPlane(this.dragPlane, target)) {
      this.jointBody.position.set(target.x, target.y, target.z);
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
    if (this.world && this.constraint) this.world.removeConstraint(this.constraint);
    this.constraint = null;
    this.setOrbit(true);
  }
}

function bboxVolume(geom: THREE.BufferGeometry): number {
  geom.computeBoundingBox();
  const b = geom.boundingBox!;
  return Math.max(b.max.x - b.min.x, 0.1) * Math.max(b.max.y - b.min.y, 0.1) * Math.max(b.max.z - b.min.z, 0.1);
}

function colliderFor(geom: THREE.BufferGeometry): { shape: CANNON.Shape; offset?: CANNON.Vec3 } {
  const pos = geom.getAttribute("position");
  if (pos && pos.count >= 4) {
    try {
      const step = Math.max(1, Math.floor(pos.count / MAX_HULL_PTS));
      const pts: THREE.Vector3[] = [];
      for (let i = 0; i < pos.count; i += step) pts.push(new THREE.Vector3().fromBufferAttribute(pos, i));
      const hull = hullPolyhedron(pts);
      if (hull) return { shape: hull };
    } catch {
      /* degenerate hull — fall through to a box */
    }
  }
  geom.computeBoundingBox();
  const b = geom.boundingBox!;
  const c = new THREE.Vector3();
  b.getCenter(c);
  const hx = Math.max((b.max.x - b.min.x) / 2, 0.5);
  const hy = Math.max((b.max.y - b.min.y) / 2, 0.5);
  const hz = Math.max((b.max.z - b.min.z) / 2, 0.5);
  return { shape: new CANNON.Box(new CANNON.Vec3(hx, hy, hz)), offset: new CANNON.Vec3(c.x, c.y, c.z) };
}

function hullPolyhedron(points: THREE.Vector3[]): CANNON.ConvexPolyhedron | null {
  const cg = new ConvexGeometry(points);
  const vp = cg.getAttribute("position");
  if (!vp) return null;
  const verts: CANNON.Vec3[] = [];
  const faces: number[][] = [];
  const map = new Map<string, number>();
  const idx = (x: number, y: number, z: number): number => {
    const k = `${x.toFixed(3)},${y.toFixed(3)},${z.toFixed(3)}`;
    let id = map.get(k);
    if (id === undefined) {
      id = verts.length;
      verts.push(new CANNON.Vec3(x, y, z));
      map.set(k, id);
    }
    return id;
  };
  for (let i = 0; i < vp.count; i += 3) {
    const a = idx(vp.getX(i), vp.getY(i), vp.getZ(i));
    const b = idx(vp.getX(i + 1), vp.getY(i + 1), vp.getZ(i + 1));
    const c = idx(vp.getX(i + 2), vp.getY(i + 2), vp.getZ(i + 2));
    if (a !== b && b !== c && a !== c) faces.push([a, b, c]);
  }
  cg.dispose();
  if (verts.length < 4 || faces.length < 4) return null;
  return new CANNON.ConvexPolyhedron({ vertices: verts, faces });
}
