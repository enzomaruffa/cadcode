import * as THREE from "three";
import { CSS2DRenderer, CSS2DObject } from "three/examples/jsm/renderers/CSS2DRenderer.js";

// Click two points -> a dimension line + a distance label (mm), rendered as an
// HTML overlay (CSS2D) above the WebGL canvas. Chains: every two picks start a
// new measurement. Points come from the picker's hit point.
export class Measure {
  private css: CSS2DRenderer;
  private group = new THREE.Group();
  private pending: THREE.Vector3[] = [];

  constructor(scene: THREE.Scene, container: HTMLElement) {
    this.css = new CSS2DRenderer();
    const el = this.css.domElement;
    el.style.position = "absolute";
    el.style.inset = "0";
    el.style.pointerEvents = "none";
    container.appendChild(el);
    scene.add(this.group);
  }

  setSize(width: number, height: number): void {
    this.css.setSize(width, height);
  }

  render(scene: THREE.Scene, camera: THREE.Camera): void {
    this.css.render(scene, camera);
  }

  addPoint(p: [number, number, number]): void {
    const v = new THREE.Vector3().fromArray(p);
    this.pending.push(v);
    this.group.add(this.dot(v));
    if (this.pending.length === 2) {
      this.drawDistance(this.pending[0], this.pending[1]);
      this.pending = [];
    }
  }

  private dot(v: THREE.Vector3): THREE.Mesh {
    const m = new THREE.Mesh(
      new THREE.SphereGeometry(0.6, 12, 12),
      new THREE.MeshBasicMaterial({ color: "#d8a657", depthTest: false }),
    );
    m.position.copy(v);
    m.renderOrder = 1003;
    return m;
  }

  private drawDistance(a: THREE.Vector3, b: THREE.Vector3): void {
    const geom = new THREE.BufferGeometry().setFromPoints([a, b]);
    const line = new THREE.Line(geom, new THREE.LineBasicMaterial({ color: "#d8a657", depthTest: false }));
    line.renderOrder = 1003;
    this.group.add(line);

    const dist = a.distanceTo(b);
    const el = document.createElement("div");
    el.className = "measure-label";
    el.textContent = `${dist.toFixed(2)} mm`;
    const label = new CSS2DObject(el);
    label.position.copy(a).add(b).multiplyScalar(0.5);
    this.group.add(label);
  }

  clear(): void {
    for (const child of [...this.group.children]) {
      this.group.remove(child);
      if (child instanceof CSS2DObject) {
        child.element.remove();
      } else {
        const wg = child as { geometry?: THREE.BufferGeometry; material?: THREE.Material };
        wg.geometry?.dispose();
        wg.material?.dispose();
      }
    }
    this.pending = [];
  }

  dispose(): void {
    this.clear();
    this.group.removeFromParent();
    this.css.domElement.remove();
  }
}
