import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";

// Thin OrbitControls wrapper. We use Orbit (not Trackball) because it applies
// synchronously on pointer events and fires "change" each move — exactly what
// on-demand rendering needs (no always-on animation loop). Z-up keeps the model
// level (no horizon tilt), which reads better for CAD than free trackball.
export class Controls {
  controls: OrbitControls;
  private onChange: () => void;

  constructor(camera: THREE.Camera, dom: HTMLElement, target: THREE.Vector3, onChange: () => void) {
    this.onChange = onChange;
    this.controls = this.make(camera, dom, target);
  }

  private make(camera: THREE.Camera, dom: HTMLElement, target: THREE.Vector3): OrbitControls {
    const c = new OrbitControls(camera, dom);
    c.enableDamping = false;
    c.screenSpacePanning = true;
    c.zoomToCursor = true;
    c.target.copy(target);
    c.addEventListener("change", this.onChange);
    c.update();
    return c;
  }

  /** Rebind to a different camera (ortho<->persp), preserving the target. */
  setCamera(camera: THREE.Camera, dom: HTMLElement): void {
    const target = this.controls.target.clone();
    this.controls.removeEventListener("change", this.onChange);
    this.controls.dispose();
    this.controls = this.make(camera, dom, target);
  }

  get target(): THREE.Vector3 {
    return this.controls.target;
  }

  syncTarget(target: THREE.Vector3): void {
    this.controls.target.copy(target);
    this.controls.update();
  }

  dispose(): void {
    this.controls.removeEventListener("change", this.onChange);
    this.controls.dispose();
  }
}
