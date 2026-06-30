import * as THREE from "three";
import type { LeafObject } from "../core/leaf";

export interface PickTargets {
  faces: THREE.Mesh[];
  edges: THREE.Object3D[]; // LineSegments2
  vertices: THREE.Points[];
}

export function collectTargets(leaves: LeafObject[]): PickTargets {
  const faces: THREE.Mesh[] = [];
  const edges: THREE.Object3D[] = [];
  const vertices: THREE.Points[] = [];
  for (const l of leaves) {
    if (l.front) faces.push(l.front);
    if (l.edges) edges.push(l.edges);
    if (l.points) vertices.push(l.points);
  }
  return { faces, edges, vertices };
}

// Given a triangle ordinal and the prefix-sum of triangles_per_face, return the
// OCP face index that owns it. ranges[i] is the first triangle of face i.
export function indexFromRanges(idx: number, ranges?: Uint32Array): number {
  if (!ranges || ranges.length < 2) return 0;
  let lo = 0;
  let hi = ranges.length - 1; // exclusive sentinel
  while (lo < hi - 1) {
    const mid = (lo + hi) >> 1;
    if (ranges[mid] <= idx) lo = mid;
    else hi = mid;
  }
  return lo;
}
