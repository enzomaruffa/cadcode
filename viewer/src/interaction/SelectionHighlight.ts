import * as THREE from "three";
import { LineSegmentsGeometry } from "three/examples/jsm/lines/LineSegmentsGeometry.js";
import { LineSegments2 } from "three/examples/jsm/lines/LineSegments2.js";
import { LineMaterial } from "three/examples/jsm/lines/LineMaterial.js";
import type { LeafObject } from "../core/leaf";

const HL = "#5fd0ff"; // cool selection wash, reads on any base color

// Persistent overlay group that visually marks the current pick: a translucent
// wash over the selected face, or a bright line for a selected edge. Built from
// standalone geometry (no shared buffers) so it survives scene rebuilds safely.
export class SelectionHighlight {
  private group = new THREE.Group();
  private resolution: THREE.Vector2;

  constructor(scene: THREE.Scene, resolution: THREE.Vector2) {
    this.resolution = resolution;
    this.group.renderOrder = 1000;
    scene.add(this.group);
  }

  showFace(leaf: LeafObject, faceIndex: number): void {
    this.clear();
    const front = leaf.front;
    const ranges = leaf.pickMeta.faceRanges;
    if (!front || !ranges) return;
    const src = front.geometry as THREE.BufferGeometry;
    const idx = src.getIndex();
    const pos = src.getAttribute("position");
    if (!idx || !pos) return;

    const start = ranges[faceIndex] * 3;
    const end = ranges[faceIndex + 1] * 3;
    const verts = new Float32Array((end - start) * 3);
    for (let i = 0; i < end - start; i++) {
      const vi = idx.getX(start + i);
      verts[i * 3] = pos.getX(vi);
      verts[i * 3 + 1] = pos.getY(vi);
      verts[i * 3 + 2] = pos.getZ(vi);
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(verts, 3));
    const m = new THREE.MeshBasicMaterial({
      color: HL,
      transparent: true,
      opacity: 0.3,
      depthWrite: false,
      side: THREE.DoubleSide,
      polygonOffset: true,
      polygonOffsetFactor: -1,
      polygonOffsetUnits: -1,
    });
    const mesh = new THREE.Mesh(g, m);
    mesh.renderOrder = 1001;
    this.placeAt(mesh, front);
    this.group.add(mesh);
  }

  showEdge(leaf: LeafObject, edgeIndex: number): void {
    this.clear();
    const edges = leaf.edges;
    const ranges = leaf.pickMeta.edgeRanges;
    if (!edges || !ranges) return;
    // The leaf's flat edge stream, sliced to this edge's segment range (6 floats/segment).
    const flat = edges.userData.edgeFlat as number[] | undefined;
    if (!flat) return;
    const start = ranges[edgeIndex] * 6;
    const end = ranges[edgeIndex + 1] * 6;
    const seg = new Float32Array(flat.slice(start, end));
    const g = new LineSegmentsGeometry();
    g.setPositions(seg);
    const mat = new LineMaterial({ color: new THREE.Color(HL).getHex(), linewidth: 3, transparent: true });
    mat.resolution.copy(this.resolution);
    const line = new LineSegments2(g, mat);
    line.frustumCulled = false;
    line.renderOrder = 1002;
    this.placeAt(line, edges);
    this.group.add(line);
  }

  private placeAt(obj: THREE.Object3D, source: THREE.Object3D): void {
    source.updateWorldMatrix(true, false);
    obj.matrixAutoUpdate = false;
    obj.matrix.copy(source.matrixWorld);
  }

  clear(): void {
    for (const child of [...this.group.children]) {
      this.group.remove(child);
      const withGeom = child as { geometry?: THREE.BufferGeometry; material?: THREE.Material };
      withGeom.geometry?.dispose();
      withGeom.material?.dispose();
    }
  }

  dispose(): void {
    this.clear();
    this.group.removeFromParent();
  }
}
