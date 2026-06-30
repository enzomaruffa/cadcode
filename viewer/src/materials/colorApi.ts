import * as THREE from "three";
import type { SceneGraph } from "../core/SceneGraph";
import type { LeafObject } from "../core/leaf";

// Must match the backend PROVENANCE_PALETTE (provenance.py) so the client-side
// active-line recolor agrees with what the server computed.
export const PROVENANCE_PALETTE = [
  "#5a7bd6",
  "#4ab0a0",
  "#c08a4a",
  "#9a6fc0",
  "#5aa0c0",
  "#b06a8a",
  "#7aa84a",
  "#c0644a",
];
export const HIGHLIGHT_GLOW = "#ffd23f";

const lineOf = (name: string): number => {
  const m = /L(\d+)__/.exec(name);
  return m ? parseInt(m[1], 10) : -1;
};

// Highlight mode: faces are per-line leaves named "L<line>__f<i>". Recolor in
// place (just material.color) so moving the editor cursor is a sub-ms tint, not
// a geometry rebuild. The face(s) from the active line glow.
export function applyHighlight(sg: SceneGraph, activeLine: number | null): void {
  const n = PROVENANCE_PALETTE.length;
  for (const leaf of sg.leaves) {
    if (!leaf.front) continue;
    const line = lineOf(leaf.pickMeta.name);
    const hex = activeLine != null && line === activeLine ? HIGHLIGHT_GLOW : PROVENANCE_PALETTE[((line % n) + n) % n];
    (leaf.front.material as THREE.MeshStandardMaterial).color.set(hex);
  }
}

// Forward path: color a single solid mesh per OCP face (no backend re-tessellate
// into N parts). Expands per-face colors across each face's triangle range via
// the cached prefix-sum, writes a vertex-color attribute, flips vertexColors on.
export function applyFaceColors(leaf: LeafObject, faceColors: string[]): void {
  const front = leaf.front;
  const ranges = leaf.pickMeta.faceRanges;
  const tpf = leaf.pickMeta.trianglesPerFace;
  if (!front || !ranges || !tpf) return;

  const geom = front.geometry as THREE.BufferGeometry;
  const index = geom.getIndex();
  const pos = geom.getAttribute("position");
  if (!index || !pos) return;

  const colors = new Float32Array(pos.count * 3);
  const c = new THREE.Color();
  for (let f = 0; f < tpf.length; f++) {
    c.set(faceColors[f] ?? "#cccccc");
    for (let t = ranges[f]; t < ranges[f + 1]; t++) {
      for (let k = 0; k < 3; k++) {
        const vi = index.getX(t * 3 + k);
        colors[vi * 3] = c.r;
        colors[vi * 3 + 1] = c.g;
        colors[vi * 3 + 2] = c.b;
      }
    }
  }
  geom.setAttribute("color", new THREE.BufferAttribute(colors, 3));
  const mat = front.material as THREE.MeshStandardMaterial;
  mat.vertexColors = true;
  mat.color.set("#ffffff");
  mat.needsUpdate = true;
}
