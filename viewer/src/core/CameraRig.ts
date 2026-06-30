import * as THREE from "three";
import type { ViewerOptions } from "./types";

const MARGIN = 1.35;
const FOV = 22;

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

  /** Frame the camera isometrically to a bbox (used on first render and `fit`). */
  fitTo(bbox: THREE.Box3): void {
    const center = bbox.getCenter(new THREE.Vector3());
    const size = bbox.getSize(new THREE.Vector3());
    this.radius = Math.max(0.5 * size.length(), 0.001);
    this.target.copy(center);

    // Iso direction for the configured up axis.
    const dir = this.up.z === 1 ? new THREE.Vector3(1, -1, 1).normalize() : new THREE.Vector3(1, 1, 1).normalize();
    const dist = this.radius * 4;

    for (const cam of [this.ortho, this.persp]) {
      cam.position.copy(center).addScaledVector(dir, dist);
      cam.near = Math.max(this.radius * 0.01, 0.01);
      cam.far = dist + this.radius * 8;
      cam.up.copy(this.up);
      cam.lookAt(center);
    }
    this.ortho.zoom = 1;
    this.persp.zoom = 1;
    this.updateProjection();
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
