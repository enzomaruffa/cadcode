/// <reference lib="webworker" />
import RAPIER from "@dimforge/rapier3d-compat";
import { FLOATS_PER_BODY, type BodyInit, type InitMsg, type InMsg, type OutMsg } from "./protocol";

// The Rapier solver, off the main thread. Owns the physics World, steps it at a
// fixed 60Hz, and streams every body's transform back to PhysicsClient. Dragging
// is a spherical joint from the grabbed body to a kinematic "cursor" the main
// thread moves — so the part is pulled toward the pointer yet still pushed and
// blocked by the others (stays dynamic).

let ready = false;
let world: RAPIER.World | null = null;
let bodies: RAPIER.RigidBody[] = [];
let transforms = new Float32Array(0);
let cursor: RAPIER.RigidBody | null = null;
let dragJoint: RAPIER.ImpulseJoint | null = null;
let init: InitMsg | null = null;
let timer: ReturnType<typeof setInterval> | null = null;
// Contact events: collider handle → body index (ground and cursor excluded), the
// currently-touching pair set, and whether it changed since the last frame.
let eventQueue: RAPIER.EventQueue | null = null;
let handleToBody = new Map<number, number>();
let contacts = new Set<number>(); // packed pair key: min * 65536 + max
let contactsDirty = false;
// Ranged joints that "play joints" can drive, with the drive state.
let drivable: { joint: RAPIER.RevoluteImpulseJoint | RAPIER.PrismaticImpulseJoint; range: [number, number] }[] = [];
let driveMode: "range-pingpong" | "off" = "off";
let drivePeriodMs = 4000;
let driveT0 = 0;
let savedGravity: { x: number; y: number; z: number } | null = null;

const post = (m: OutMsg) => (self as unknown as Worker).postMessage(m);

self.onmessage = async (e: MessageEvent<InMsg>) => {
  const m = e.data;
  if (m.type === "init") {
    try {
      await onInit(m);
    } catch (err) {
      post({ type: "error", error: String(err) });
    }
    return;
  }
  if (!world) return;
  if (m.type === "grab") onGrab(m.index, m.pivot);
  else if (m.type === "move") onMove(m.point);
  else if (m.type === "release") onRelease();
  else if (m.type === "reset") onReset();
  else if (m.type === "drive") onDrive(m.mode, m.period_s);
  else if (m.type === "stop") teardown();
};

async function onInit(m: InitMsg): Promise<void> {
  if (!ready) {
    await RAPIER.init();
    ready = true;
  }
  teardown();
  init = m;
  world = new RAPIER.World({ x: m.gravity[0], y: m.gravity[1], z: m.gravity[2] });
  world.timestep = 1 / 60;

  // Ground: a big thin fixed slab with its top face at groundZ (Z-up).
  const gb = world.createRigidBody(RAPIER.RigidBodyDesc.fixed().setTranslation(0, 0, m.groundZ - 0.5));
  world.createCollider(RAPIER.ColliderDesc.cuboid(m.extent, m.extent, 0.5).setFriction(0.7).setRestitution(0.04), gb);

  bodies = [];
  handleToBody = new Map();
  contacts = new Set();
  contactsDirty = false;
  for (const b of m.bodies) {
    const rb = world.createRigidBody(
      RAPIER.RigidBodyDesc.dynamic()
        .setTranslation(b.pos[0], b.pos[1], b.pos[2])
        .setRotation({ x: b.quat[0], y: b.quat[1], z: b.quat[2], w: b.quat[3] })
        .setLinearDamping(0.1)
        .setAngularDamping(0.3)
        .setCcdEnabled(b.ccd),
    );
    const col = colliderFor(b);
    if (col) {
      col.setFriction(0.7).setRestitution(0.04).setDensity(m.density);
      col.setActiveEvents(RAPIER.ActiveEvents.COLLISION_EVENTS);
      const collider = world.createCollider(col, rb);
      handleToBody.set(collider.handle, bodies.length);
    }
    bodies.push(rb);
  }
  transforms = new Float32Array(bodies.length * FLOATS_PER_BODY);
  eventQueue = new RAPIER.EventQueue(true);

  // Articulation: the build123d joint graph → Rapier constraints. A revolute
  // joint becomes a hinge (grab a linked part, it swings on its real axis);
  // prismatic a slider; fixed a weld. Motion ranges become joint limits.
  for (const j of m.joints) {
    const a = bodies[j.a];
    const b = bodies[j.b];
    if (!a || !b) continue;
    const anchorA = { x: j.anchorA[0], y: j.anchorA[1], z: j.anchorA[2] };
    const anchorB = { x: j.anchorB[0], y: j.anchorB[1], z: j.anchorB[2] };
    const axis = { x: j.axis[0], y: j.axis[1], z: j.axis[2] };
    let params: RAPIER.JointData;
    if (j.kind === "revolute" || j.kind === "cylindrical") params = RAPIER.JointData.revolute(anchorA, anchorB, axis);
    else if (j.kind === "prismatic") params = RAPIER.JointData.prismatic(anchorA, anchorB, axis);
    else if (j.kind === "spherical") params = RAPIER.JointData.spherical(anchorA, anchorB);
    else params = RAPIER.JointData.fixed(anchorA, { w: 1, x: 0, y: 0, z: 0 }, anchorB, { w: 1, x: 0, y: 0, z: 0 });
    const joint = world.createImpulseJoint(params, a, b, true);
    // Jointed pairs always touch at their axis — without this, contact events
    // paint every hinge permanently red.
    joint.setContactsEnabled(false);
    if (j.range && (j.kind === "revolute" || j.kind === "prismatic")) {
      const rad = j.kind === "revolute" ? Math.PI / 180 : 1;
      const jj = joint as RAPIER.RevoluteImpulseJoint | RAPIER.PrismaticImpulseJoint;
      if (typeof jj.setLimits === "function") jj.setLimits(j.range[0] * rad, j.range[1] * rad);
      drivable.push({ joint: jj, range: [j.range[0] * rad, j.range[1] * rad] });
    }
  }

  // Kinematic drag target (moved by the pointer via setNextKinematicTranslation).
  cursor = world.createRigidBody(RAPIER.RigidBodyDesc.kinematicPositionBased());

  post({ type: "ready", bodies: bodies.length });
  start();
}

function colliderFor(b: BodyInit): RAPIER.ColliderDesc | null {
  // Concave parts arrive pre-voxelized (occupied-voxel centers) — a sparse voxel
  // collider follows the real surface (hollow bowl, perforated dish) where a
  // convex hull would fill the cavity. Convex parts stay a smooth single hull.
  if (b.voxels && b.voxels.length >= 3 && b.voxelSize > 0) {
    try {
      const vs = { x: b.voxelSize, y: b.voxelSize, z: b.voxelSize };
      const d = RAPIER.ColliderDesc.voxels(b.voxels, vs);
      if (d) return d;
    } catch {
      /* fall through to a hull */
    }
  }
  const hull = RAPIER.ColliderDesc.convexHull(b.points);
  if (hull) return hull;
  // Degenerate points → a ball sized to the volume, so the body still collides.
  return RAPIER.ColliderDesc.ball(Math.max(Math.cbrt(b.volume) * 0.5, 0.5));
}

const FIXED_MS = 1000 / 60;
let last = 0;
let acc = 0;

function start(): void {
  stop();
  last = performance.now();
  acc = 0;
  timer = setInterval(tick, FIXED_MS);
}
function stop(): void {
  if (timer) clearInterval(timer);
  timer = null;
}

// Accumulator loop: workers have no rAF and their timers throttle in hidden
// tabs, so we catch up in fixed steps against a real clock (clamped so a long
// stall can't trigger a spiral of death).
function tick(): void {
  if (!world) return;
  const now = performance.now();
  acc = Math.min(acc + (now - last), 250);
  last = now;
  let stepped = false;
  while (acc >= FIXED_MS) {
    if (driveMode === "range-pingpong") driveJoints(now);
    world.step(eventQueue ?? undefined);
    drainContacts();
    acc -= FIXED_MS;
    stepped = true;
  }
  if (!stepped) return;
  for (let i = 0; i < bodies.length; i++) {
    const t = bodies[i].translation();
    const r = bodies[i].rotation();
    const o = i * FLOATS_PER_BODY;
    transforms[o] = t.x;
    transforms[o + 1] = t.y;
    transforms[o + 2] = t.z;
    transforms[o + 3] = r.x;
    transforms[o + 4] = r.y;
    transforms[o + 5] = r.z;
    transforms[o + 6] = r.w;
  }
  const frame: { type: "frame"; transforms: Float32Array; contacts?: Int32Array } = {
    type: "frame",
    transforms: transforms.slice(0),
  };
  if (contactsDirty) {
    const flat = new Int32Array(contacts.size * 2);
    let k = 0;
    for (const key of contacts) {
      flat[k++] = Math.floor(key / 65536);
      flat[k++] = key % 65536;
    }
    frame.contacts = flat;
    contactsDirty = false;
  }
  post(frame);
}

function drainContacts(): void {
  eventQueue?.drainCollisionEvents((h1, h2, started) => {
    const a = handleToBody.get(h1);
    const b = handleToBody.get(h2);
    if (a === undefined || b === undefined) return; // ground / cursor involved
    const key = Math.min(a, b) * 65536 + Math.max(a, b);
    const had = contacts.has(key);
    if (started && !had) {
      contacts.add(key);
      contactsDirty = true;
    } else if (!started && had) {
      contacts.delete(key);
      contactsDirty = true;
    }
  });
}

// Time-based triangle wave through each ranged joint's limits (Rapier 0.19 has
// no joint-angle getter to close the loop on). Stiff position motor, soft
// damping — the joint chases the target but still yields to real collisions,
// which is exactly what makes the contact flash honest.
function driveJoints(now: number): void {
  const phase = ((now - driveT0) % drivePeriodMs) / drivePeriodMs; // 0..1
  const tri = phase < 0.5 ? phase * 2 : 2 - phase * 2; // 0→1→0
  for (const d of drivable) {
    const target = d.range[0] + (d.range[1] - d.range[0]) * tri;
    d.joint.configureMotorPosition(target, 1e6, 1e4);
  }
  for (const b of bodies) b.wakeUp();
}

function onDrive(mode: "range-pingpong" | "off", period_s?: number): void {
  driveMode = mode;
  drivePeriodMs = Math.max(0.5, period_s ?? 4) * 1000;
  driveT0 = performance.now();
  if (!world) return;
  if (mode === "range-pingpong") {
    // Park gravity while driving so the mechanism sweeps instead of sagging.
    if (!savedGravity) savedGravity = { x: world.gravity.x, y: world.gravity.y, z: world.gravity.z };
    world.gravity = { x: 0, y: 0, z: 0 };
  } else {
    for (const d of drivable) d.joint.configureMotorVelocity(0, 0);
    if (savedGravity) {
      world.gravity = savedGravity;
      savedGravity = null;
    }
  }
}

function onGrab(index: number, pivot: [number, number, number]): void {
  if (!world || !cursor || index < 0 || index >= bodies.length) return;
  onRelease();
  const body = bodies[index];
  // Park the cursor at the body's current world pivot so the joint doesn't yank.
  const t = body.translation();
  const rot = body.rotation();
  const wp = rotate(pivot, rot);
  cursor.setNextKinematicTranslation({ x: t.x + wp[0], y: t.y + wp[1], z: t.z + wp[2] });
  const params = RAPIER.JointData.spherical({ x: pivot[0], y: pivot[1], z: pivot[2] }, { x: 0, y: 0, z: 0 });
  dragJoint = world.createImpulseJoint(params, body, cursor, true);
  body.wakeUp();
}

function onMove(point: [number, number, number]): void {
  cursor?.setNextKinematicTranslation({ x: point[0], y: point[1], z: point[2] });
}

function onRelease(): void {
  if (world && dragJoint) world.removeImpulseJoint(dragJoint, true);
  dragJoint = null;
}

function onReset(): void {
  if (!init) return;
  onRelease();
  init.bodies.forEach((b, i) => {
    const rb = bodies[i];
    if (!rb) return;
    rb.setTranslation({ x: b.pos[0], y: b.pos[1], z: b.pos[2] }, true);
    rb.setRotation({ x: b.quat[0], y: b.quat[1], z: b.quat[2], w: b.quat[3] }, true);
    rb.setLinvel({ x: 0, y: 0, z: 0 }, true);
    rb.setAngvel({ x: 0, y: 0, z: 0 }, true);
  });
}

function teardown(): void {
  stop();
  onRelease();
  eventQueue?.free();
  eventQueue = null;
  world?.free();
  world = null;
  bodies = [];
  cursor = null;
  drivable = [];
  driveMode = "off";
  savedGravity = null;
  handleToBody = new Map();
  contacts = new Set();
  contactsDirty = false;
}

/** Rotate a local vector by a quaternion (q · v · q⁻¹), no THREE in the worker. */
function rotate(v: [number, number, number], q: { x: number; y: number; z: number; w: number }): [number, number, number] {
  const ix = q.w * v[0] + q.y * v[2] - q.z * v[1];
  const iy = q.w * v[1] + q.z * v[0] - q.x * v[2];
  const iz = q.w * v[2] + q.x * v[1] - q.y * v[0];
  const iw = -q.x * v[0] - q.y * v[1] - q.z * v[2];
  return [
    ix * q.w + iw * -q.x + iy * -q.z - iz * -q.y,
    iy * q.w + iw * -q.y + iz * -q.x - ix * -q.z,
    iz * q.w + iw * -q.z + ix * -q.y - iy * -q.x,
  ];
}
