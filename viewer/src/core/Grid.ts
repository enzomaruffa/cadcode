import * as THREE from "three";

// A ground grid in the XY plane (Z-up), sized to the model, that gives a sense
// of scale. Toggleable; re-fit to the bbox on each render.
export class Grid {
  private group = new THREE.Group();
  private grid: THREE.GridHelper | null = null;
  private enabled = true;

  constructor(scene: THREE.Scene) {
    scene.add(this.group);
  }

  setEnabled(on: boolean): void {
    this.enabled = on;
    this.group.visible = on;
  }

  fitTo(bbox: THREE.Box3): void {
    this.clearGrid();
    const size = bbox.getSize(new THREE.Vector3());
    const span = Math.max(size.x, size.y, 1);
    const extent = Math.max(Math.ceil((span * 2.5) / 10) * 10, 10);
    const divisions = 20;

    const g = new THREE.GridHelper(extent, divisions, 0xb59460, 0x554d3d);
    g.rotateX(Math.PI / 2); // XZ -> XY plane for Z-up
    const c = bbox.getCenter(new THREE.Vector3());
    g.position.set(c.x, c.y, bbox.min.z);
    const mat = g.material as THREE.Material;
    mat.transparent = true;
    mat.opacity = 0.6;
    mat.depthWrite = false;
    g.renderOrder = -1; // behind the model
    this.grid = g;
    this.group.add(g);
    this.group.visible = this.enabled;
  }

  private clearGrid(): void {
    if (!this.grid) return;
    this.group.remove(this.grid);
    this.grid.geometry.dispose();
    (this.grid.material as THREE.Material).dispose();
    this.grid = null;
  }

  dispose(): void {
    this.clearGrid();
    this.group.removeFromParent();
  }
}
