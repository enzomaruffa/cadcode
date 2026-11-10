import * as THREE from "three";
import type { ViewerOptions } from "./types";

const MARGIN = 1.35;
const FIT_PAD = 1.4; // breathing room around the projected bbox on fit
const FOV = 22;
const AXIS_Y = new THREE.Vector3(0, 1, 0);
const AXIS_Z = new THREE.Vector3(0, 0, 1);

// Combined orthographic + perspective camera with a Z-up, iso default framing
// (FreeCAD/OnShape convention) and absolute camera-state save/restore so
// re-rendering on every edit never disturbs the view.
export class CameraRig {
  readonly ortho: THREE.OrthographicCamera;
  readonly persp: THREE.PerspectiveCamera;
  isOrtho: boolean;
  readonly target = new THREE.Vector3();
  private radius = 1;
  private aspect = 1;
  private readonly up: THREE.Vector3;
  private readonly viewBack = new THREE.Vector3();
  private readonly eyeOffset = new THREE.Vector3();

  constructor(opts: ViewerOptions) {
    this.up = opts.up === "Y" ? new THREE.Vector3(0, 1, 0) : new THREE.Vector3(0, 0, 1);
    this.isOrtho = opts.ortho !== false;

    this.ortho = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.01, 100);
    this.persp = new THREE.PerspectiveCamera(FOV, 1, 0.01, 100);
    this.ortho.up.copy(this.up);
    this.persp.up.copy(this.up);
  }

  get camera(): THREE.OrthographicCamera | THREE.PerspectiveCamera {
    return this.isOrtho ? this.ortho : this.persp;
  }

  setAspect(width: number, height: number): void {
    this.aspect = height > 0 ? width / height : 1;
    this.updateProjection();
  }

  private updateProjection(): void {
    const h = this.radius * MARGIN;
    const w = h * this.aspect;
    this.ortho.top = h;
    this.ortho.bottom = -h;
    this.ortho.left = -w;
    this.ortho.right = w;
    this.ortho.updateProjectionMatrix();

    this.persp.aspect = this.aspect;
    this.persp.updateProjectionMatrix();
  }

  /** Frame a bbox looking along `-dir` (camera sits at center + dir); iso when omitted. */
  fitTo(bbox: THREE.Box3, dir?: THREE.Vector3): void {
    const center = bbox.getCenter(new THREE.Vector3());
    const size = bbox.getSize(new THREE.Vector3());
    this.radius = Math.max(0.5 * size.length(), 0.001);
    this.target.copy(center);

    const view = (
      dir?.clone() ?? (this.up.z === 1 ? new THREE.Vector3(1, -1, 1) : new THREE.Vector3(1, 1, 1))
    ).normalize();
    // Straight down/up the up axis makes lookAt degenerate; nudge so screen-up stays +Y.
    if (Math.abs(view.dot(this.up)) > 0.9999)
      view.addScaledVector(this.up.z === 1 ? AXIS_Y : AXIS_Z, -1e-3).normalize();
    const dist = this.radius * 4;

    for (const cam of [this.ortho, this.persp]) {
      cam.position.copy(center).addScaledVector(view, dist);
      cam.near = Math.max(this.radius * 0.01, 0.01);
      cam.far = dist + this.radius * 8;
      cam.up.copy(this.up);
      cam.lookAt(center);
      cam.updateMatrixWorld();
    }
    this.ortho.zoom = 1;
    this.persp.zoom = 1;
    this.updateProjection();
    this.tighten(bbox);
  }

  // The bounding sphere wastes a lot of screen on boxy models seen along an axis,
  // so frame the bbox corners as actually projected for this view.
  private tighten(bbox: THREE.Box3): void {
    const inv = this.ortho.matrixWorldInverse;
    let hx = 0;
    let hy = 0;
    let hz = 0;
    const p = new THREE.Vector3();
    for (let i = 0; i < 8; i++) {
      p.set(i & 1 ? bbox.max.x : bbox.min.x, i & 2 ? bbox.max.y : bbox.min.y, i & 4 ? bbox.max.z : bbox.min.z);
      p.applyMatrix4(inv);
      hx = Math.max(hx, Math.abs(p.x));
      hy = Math.max(hy, Math.abs(p.y));
      hz = Math.max(hz, Math.abs(p.z + this.radius * 4));
    }
    hx = Math.max(hx * FIT_PAD, 1e-3);
    hy = Math.max(hy * FIT_PAD, 1e-3);
    this.ortho.zoom = Math.min(this.ortho.top / hy, this.ortho.right / hx);
    this.ortho.updateProjectionMatrix();

    const tanV = Math.tan(THREE.MathUtils.degToRad(FOV / 2));
    const tanH = tanV * this.aspect;
    const dist = Math.max(hy / tanV, hx / tanH) + hz;
    const dir = this.persp.position.clone().sub(this.target).normalize();
    this.persp.position.copy(this.target).addScaledVector(dir, dist);
    this.persp.far = Math.max(this.persp.far, dist + this.radius * 4);
    this.persp.updateProjectionMatrix();
  }

  /** Fit near/far around everything drawn, every frame: zooming, panning and a camera
   *  restored from another document all move the eye relative to the last fit. */
  updateClipping(bounds: THREE.Sphere): void {
    const cam = this.camera;
    const r = Math.max(bounds.radius, 1e-3);
    const back = this.viewBack.setFromMatrixColumn(cam.matrixWorld, 2);
    let s = this.eyeOffset.copy(cam.position).sub(bounds.center).dot(back);
    if (this.isOrtho && s < r * 1.5) {
      // Sliding an ortho eye along its view axis leaves the image unchanged.
      cam.position.addScaledVector(back, r * 1.5 - s);
      cam.updateMatrixWorld();
      s = r * 1.5;
    }
    const near = this.isOrtho ? Math.max(s - r * 1.2, 1e-3) : Math.max(s - r * 1.2, s * 0.005, 1e-3);
    const far = s + r * 1.2;
    if (cam.near !== near || cam.far !== far) {
      cam.near = near;
      cam.far = far;
      cam.updateProjectionMatrix();
    }
  }

  restore(opts: ViewerOptions): void {
    const cam = this.camera;
    if (opts.position) cam.position.fromArray(opts.position);
    if (opts.quaternion) cam.quaternion.fromArray(opts.quaternion);
    if (opts.target) this.target.fromArray(opts.target);
    if (typeof opts.zoom === "number") cam.zoom = opts.zoom;
    cam.updateProjectionMatrix();
  }

  getState(): Required<Pick<ViewerOptions, "position" | "quaternion" | "target" | "zoom">> {
    const cam = this.camera;
    return {
      position: cam.position.toArray(),
      quaternion: cam.quaternion.toArray(),
      target: this.target.toArray(),
      zoom: cam.zoom,
    };
  }

  get bbRadius(): number {
    return this.radius;
  }
}
