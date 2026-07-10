import * as THREE from "three";

// A measured "cutting mat" under (and optionally behind) the model — the ruler
// background: adaptive 1/2/5×10ⁿ tick spacing, minor/major lines, and numeric
// labels in REAL world coordinates baked into the texture (they lie flat in the
// plane like a printed mat). Modes: off | floor | walls (floor + two measured
// back walls so heights read as easily as footprints). Re-fit per render.

export type GridMode = "off" | "floor" | "walls";

const TEX = 2048; // canvas resolution per plane
const LINE_MINOR = "rgba(154, 141, 121, 0.28)";
const LINE_MAJOR = "rgba(196, 178, 148, 0.55)";
const LINE_AXIS_X = "rgba(224, 119, 94, 0.8)"; // warm red — X through origin
const LINE_AXIS_Y = "rgba(123, 191, 106, 0.75)"; // sage — Y through origin
const LABEL = "rgba(206, 192, 168, 0.9)";
const LABEL_UNIT = "rgba(156, 148, 138, 0.8)";

/** Nice ruler step (1/2/5 × 10ⁿ) so ~`target` majors span `span`. */
function niceStep(span: number, target = 8): number {
  const raw = Math.max(span / target, 1e-6);
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  for (const m of [1, 2, 5, 10]) {
    if (m * mag >= raw) return m * mag;
  }
  return 10 * mag;
}

const fmt = (v: number): string => {
  const r = Math.round(v * 100) / 100;
  return Object.is(r, -0) ? "0" : String(r);
};

interface PlaneSpec {
  // world range covered by the texture: u → first axis, v → second axis
  u0: number;
  u1: number;
  v0: number;
  v1: number;
  step: number;
  uAxis: "X" | "Y" | "Z";
  vAxis: "X" | "Y" | "Z";
  axisLines: boolean; // tint the u=0 / v=0 lines (floor only)
}

/** Draw a ruler plane onto a canvas: minor/major grid + coordinate labels. */
function drawRuler(spec: PlaneSpec): HTMLCanvasElement {
  const canvas = document.createElement("canvas");
  canvas.width = TEX;
  canvas.height = TEX;
  const ctx = canvas.getContext("2d")!;
  const { u0, u1, v0, v1, step } = spec;
  const sx = TEX / (u1 - u0);
  const sy = TEX / (v1 - v0);
  const px = (u: number) => (u - u0) * sx;
  const py = (v: number) => TEX - (v - v0) * sy; // canvas y is down; world v is up

  const minor = step / 5;
  ctx.clearRect(0, 0, TEX, TEX);

  const line = (a: number, vertical: boolean, style: string, width: number) => {
    ctx.strokeStyle = style;
    ctx.lineWidth = width;
    ctx.beginPath();
    if (vertical) {
      ctx.moveTo(px(a), 0);
      ctx.lineTo(px(a), TEX);
    } else {
      ctx.moveTo(0, py(a));
      ctx.lineTo(TEX, py(a));
    }
    ctx.stroke();
  };

  // minor grid
  for (let u = Math.ceil(u0 / minor) * minor; u <= u1 + 1e-9; u += minor) line(u, true, LINE_MINOR, 1);
  for (let v = Math.ceil(v0 / minor) * minor; v <= v1 + 1e-9; v += minor) line(v, false, LINE_MINOR, 1);
  // major grid
  for (let u = Math.ceil(u0 / step) * step; u <= u1 + 1e-9; u += step) line(u, true, LINE_MAJOR, 2);
  for (let v = Math.ceil(v0 / step) * step; v <= v1 + 1e-9; v += step) line(v, false, LINE_MAJOR, 2);
  // origin axes (floor)
  if (spec.axisLines) {
    if (u0 <= 0 && u1 >= 0) line(0, true, LINE_AXIS_Y, 3); // x=0 → the Y axis
    if (v0 <= 0 && v1 >= 0) line(0, false, LINE_AXIS_X, 3); // y=0 → the X axis
  }

  // labels on ALL FOUR edges (like a real cutting mat — some edge is always
  // visible whatever the orbit), true world coordinates.
  const fontPx = Math.max(Math.min(step * sx * 0.32, 64), 22);
  ctx.font = `500 ${fontPx}px ui-monospace, Menlo, monospace`;
  ctx.fillStyle = LABEL;
  const inset = fontPx + 10;
  for (let u = Math.ceil(u0 / step) * step; u <= u1 + 1e-9; u += step) {
    ctx.textAlign = "center";
    ctx.textBaseline = "bottom";
    ctx.fillText(fmt(u), px(u), TEX - 6);
    ctx.textBaseline = "top";
    ctx.fillText(fmt(u), px(u), 6);
  }
  ctx.textBaseline = "middle";
  for (let v = Math.ceil(v0 / step) * step; v <= v1 + 1e-9; v += step) {
    const y = py(v);
    if (y < inset || y > TEX - inset) continue; // corners belong to the u rows
    ctx.textAlign = "left";
    ctx.fillText(fmt(v), 8, y);
    ctx.textAlign = "right";
    ctx.fillText(fmt(v), TEX - 8, y);
  }
  // unit + axis tag just inside the corner (below the top label row)
  ctx.fillStyle = LABEL_UNIT;
  ctx.textAlign = "right";
  ctx.textBaseline = "top";
  ctx.fillText(`${spec.uAxis}/${spec.vAxis} mm`, TEX - 10, inset + 6);
  return canvas;
}

function matFor(canvas: HTMLCanvasElement): THREE.MeshBasicMaterial {
  const tex = new THREE.CanvasTexture(canvas);
  tex.anisotropy = 8;
  tex.colorSpace = THREE.SRGBColorSpace;
  return new THREE.MeshBasicMaterial({
    map: tex,
    transparent: true,
    depthWrite: false,
    side: THREE.DoubleSide,
    toneMapped: false,
  });
}

export class Grid {
  private group = new THREE.Group();
  private planes: THREE.Mesh[] = [];
  private mode: GridMode = "floor";
  private lastBBox: THREE.Box3 | null = null;

  constructor(scene: THREE.Scene) {
    this.group.renderOrder = -1;
    scene.add(this.group);
  }

  /** Back-compat boolean toggle (true = floor) or an explicit mode. */
  setEnabled(on: boolean | GridMode): void {
    this.mode = typeof on === "boolean" ? (on ? "floor" : "off") : on;
    if (this.lastBBox) this.fitTo(this.lastBBox);
    this.group.visible = this.mode !== "off";
  }

  fitTo(bbox: THREE.Box3): void {
    this.clear();
    this.lastBBox = bbox.clone();
    if (this.mode === "off") {
      this.group.visible = false;
      return;
    }
    const size = bbox.getSize(new THREE.Vector3());
    const c = bbox.getCenter(new THREE.Vector3());
    const span = Math.max(size.x, size.y, 10);
    const step = niceStep(span * 1.6);
    // world-aligned extents: snap to step multiples so lines sit on real coords
    const half = Math.max(span * 1.1, step * 4);
    const u0 = Math.floor((c.x - half) / step) * step;
    const u1 = Math.ceil((c.x + half) / step) * step;
    const v0 = Math.floor((c.y - half) / step) * step;
    const v1 = Math.ceil((c.y + half) / step) * step;

    // floor (XY plane at the model's base)
    const floor = new THREE.Mesh(
      new THREE.PlaneGeometry(u1 - u0, v1 - v0),
      matFor(drawRuler({ u0, u1, v0, v1, step, uAxis: "X", vAxis: "Y", axisLines: true })),
    );
    floor.position.set((u0 + u1) / 2, (v0 + v1) / 2, bbox.min.z - 0.05);
    floor.renderOrder = -1;
    this.planes.push(floor);

    if (this.mode === "walls") {
      const h0 = Math.floor(bbox.min.z / step) * step;
      const h1 = Math.ceil((bbox.min.z + Math.max(size.z * 1.35, step * 3)) / step) * step;
      // back wall (XZ plane at the far +Y edge) — read X/Z
      const back = new THREE.Mesh(
        new THREE.PlaneGeometry(u1 - u0, h1 - h0),
        matFor(drawRuler({ u0, u1, v0: h0, v1: h1, step, uAxis: "X", vAxis: "Z", axisLines: false })),
      );
      back.rotation.x = Math.PI / 2;
      back.position.set((u0 + u1) / 2, v1, (h0 + h1) / 2);
      // left wall (YZ plane at the far -X edge) — read Y/Z
      const left = new THREE.Mesh(
        new THREE.PlaneGeometry(v1 - v0, h1 - h0),
        matFor(drawRuler({ u0: v0, u1: v1, v0: h0, v1: h1, step, uAxis: "Y", vAxis: "Z", axisLines: false })),
      );
      left.rotation.y = Math.PI / 2;
      left.rotation.z = Math.PI / 2;
      left.position.set(u0, (v0 + v1) / 2, (h0 + h1) / 2);
      this.planes.push(back, left);
    }

    for (const p of this.planes) this.group.add(p);
    this.group.visible = true;
  }

  private clear(): void {
    for (const p of this.planes) {
      this.group.remove(p);
      p.geometry.dispose();
      const m = p.material as THREE.MeshBasicMaterial;
      m.map?.dispose();
      m.dispose();
    }
    this.planes = [];
  }

  dispose(): void {
    this.clear();
    this.group.removeFromParent();
  }
}
