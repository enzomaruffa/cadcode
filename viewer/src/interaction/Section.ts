import * as THREE from "three";

export type SectionAxis = "x" | "y" | "z";

const AXIS_VEC: Record<SectionAxis, THREE.Vector3> = {
  x: new THREE.Vector3(1, 0, 0),
  y: new THREE.Vector3(0, 1, 0),
  z: new THREE.Vector3(0, 0, 1),
};

// A single world-space clipping plane along X/Y/Z with a draggable offset, plus
// a faint quad indicator showing where the cut sits. Keeps the half of the model
// below the cut visible. (No stencil cap in v1 — the cross-section reads hollow.)
export class Section {
  readonly plane = new THREE.Plane(new THREE.Vector3(0, 0, -1), 0);
  private helper: THREE.Mesh;
  private axis: SectionAxis = "z";
  private offset = 0.5;
  private enabled = false;
  private bbox = new THREE.Box3(new THREE.Vector3(-1, -1, -1), new THREE.Vector3(1, 1, 1));

  constructor(scene: THREE.Scene) {
    const mat = new THREE.MeshBasicMaterial({
      color: "#d8a657",
      transparent: true,
      opacity: 0.1,
      side: THREE.DoubleSide,
      depthWrite: false,
    });
    this.helper = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), mat);
    this.helper.visible = false;
    this.helper.renderOrder = 1000;
    scene.add(this.helper);
  }

  get planes(): THREE.Plane[] {
    return this.enabled ? [this.plane] : [];
  }
  get active(): boolean {
    return this.enabled;
  }

  fitTo(bbox: THREE.Box3): void {
    this.bbox.copy(bbox);
    if (this.enabled) this.update();
  }

  set(axis: SectionAxis, offset: number): void {
    this.axis = axis;
    this.offset = Math.min(Math.max(offset, 0), 1);
    this.enabled = true;
    this.update();
  }

  disable(): void {
    this.enabled = false;
    this.helper.visible = false;
  }

  private update(): void {
    const min = this.bbox.min[this.axis];
    const max = this.bbox.max[this.axis];
    const c = THREE.MathUtils.lerp(min, max, this.offset);

    // Keep the half with coord < c: normal = -axis, constant = c.
    const n = AXIS_VEC[this.axis].clone().multiplyScalar(-1);
    this.plane.set(n, c);

    // Position + orient the indicator quad at the cut, sized to the cross-section.
    const center = this.bbox.getCenter(new THREE.Vector3());
    const size = this.bbox.getSize(new THREE.Vector3());
    center[this.axis] = c;
    this.helper.position.copy(center);
    this.helper.quaternion.setFromUnitVectors(new THREE.Vector3(0, 0, 1), AXIS_VEC[this.axis]);
    if (this.axis === "z") this.helper.scale.set(size.x * 1.05, size.y * 1.05, 1);
    else if (this.axis === "y") this.helper.scale.set(size.x * 1.05, size.z * 1.05, 1);
    else this.helper.scale.set(size.z * 1.05, size.y * 1.05, 1);
    this.helper.visible = true;
  }

  dispose(): void {
    this.helper.geometry.dispose();
    (this.helper.material as THREE.Material).dispose();
    this.helper.removeFromParent();
  }
}
