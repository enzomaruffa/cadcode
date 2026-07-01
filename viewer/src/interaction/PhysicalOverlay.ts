import * as THREE from "three";
import { CSS2DRenderer, CSS2DObject } from "three/examples/jsm/renderers/CSS2DRenderer.js";

// Physical-properties overlay (mass/COM readout). Draws a center-of-mass marker,
// the ground support polygon (green = stable, red = tips), a plumb line from the
// COM down to the build plate, and an HTML label — all rendered above the WebGL
// canvas via CSS2D, mirroring the Measure tool's structure. It never recolors or
// rebuilds the model; it only adds annotation on top of the technical shading.
export interface PhysicalData {
  com: [number, number, number];
  footprint: [number, number][];
  base_z: number;
  stable: boolean;
  mass_g: number;
  tip_angle: number | null;
  material_name: string;
}

const STABLE = "#3fb950";
const TIPS = "#f85149";
const COM_COLOR = "#e5c07b";

export class PhysicalOverlay {
  private css: CSS2DRenderer;
  private group = new THREE.Group();

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

  set(data: PhysicalData): void {
    this.clear();
    const [cx, cy, cz] = data.com;
    const color = data.stable ? STABLE : TIPS;

    // Marker size scales with the footprint extent so it reads at any part size.
    let extent = 10;
    if (data.footprint.length) {
      const xs = data.footprint.map((p) => p[0]);
      const ys = data.footprint.map((p) => p[1]);
      extent = Math.max(Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys), 1);
    }
    const r = Math.max(extent * 0.03, 0.6);

    // COM marker (always visible — depthTest off, high render order).
    const marker = new THREE.Mesh(
      new THREE.SphereGeometry(r, 20, 20),
      new THREE.MeshBasicMaterial({ color: COM_COLOR, depthTest: false }),
    );
    marker.position.set(cx, cy, cz);
    marker.renderOrder = 1004;
    this.group.add(marker);

    // Support polygon at the build plate.
    if (data.footprint.length >= 2) {
      const pts = data.footprint.map((p) => new THREE.Vector3(p[0], p[1], data.base_z));
      const geom = new THREE.BufferGeometry().setFromPoints(pts);
      const loop = new THREE.LineLoop(geom, new THREE.LineBasicMaterial({ color, depthTest: false }));
      loop.renderOrder = 1003;
      this.group.add(loop);
    }

    // Plumb line from the COM to its ground projection.
    const drop = new THREE.BufferGeometry().setFromPoints([
      new THREE.Vector3(cx, cy, cz),
      new THREE.Vector3(cx, cy, data.base_z),
    ]);
    const dropLine = new THREE.LineSegments(
      drop,
      new THREE.LineDashedMaterial({ color, dashSize: r * 1.5, gapSize: r, depthTest: false }),
    );
    dropLine.computeLineDistances();
    dropLine.renderOrder = 1003;
    this.group.add(dropLine);

    // Label.
    const el = document.createElement("div");
    el.className = "physical-label";
    const tip = data.tip_angle == null ? "—" : `${data.tip_angle.toFixed(0)}°`;
    el.textContent = `${data.mass_g.toFixed(1)} g · ${data.material_name} · tips @ ${tip}`;
    const label = new CSS2DObject(el);
    label.position.set(cx, cy, cz + r * 2);
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
  }

  dispose(): void {
    this.clear();
    this.group.removeFromParent();
    this.css.domElement.remove();
  }
}
