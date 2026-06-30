import * as THREE from "three";
import type { LeafShape } from "./types";

// Some arrays (notably `edges`) arrive nested — each segment is [[x,y,z],[x,y,z]]
// — while others are already flat. Flatten to a plain number list either way.
export function flattenNumbers(a: unknown): number[] {
  const out: number[] = [];
  const rec = (x: unknown): void => {
    if (Array.isArray(x)) {
      for (const v of x) rec(v);
    } else if (typeof x === "number") {
      out.push(x);
    }
  };
  rec(a);
  return out;
}

// Flat JSON arrays -> one indexed BufferGeometry. We keep it indexed and never
// merge across leaves or split per-face, so raycast `faceIndex` maps 1:1 to a
// triangle ordinal (the prefix-sum of triangles_per_face then gives the OCP
// face) — the foundation of reliable per-face picking.
export function buildMeshGeometry(shape: LeafShape): THREE.BufferGeometry {
  const geom = new THREE.BufferGeometry();
  geom.setAttribute("position", new THREE.BufferAttribute(new Float32Array(shape.vertices), 3));
  if (shape.normals && shape.normals.length === shape.vertices.length) {
    geom.setAttribute("normal", new THREE.BufferAttribute(new Float32Array(shape.normals), 3));
  } else {
    geom.computeVertexNormals();
  }
  geom.setIndex(new THREE.BufferAttribute(new Uint32Array(shape.triangles), 1));
  geom.computeBoundingBox();
  geom.computeBoundingSphere();
  return geom;
}

/** Prefix sum of triangles_per_face: faceStart[j] is the first triangle of face j. */
export function buildFaceRanges(trianglesPerFace: number[] | undefined): Uint32Array | undefined {
  if (!trianglesPerFace || trianglesPerFace.length === 0) return undefined;
  const ranges = new Uint32Array(trianglesPerFace.length + 1);
  let acc = 0;
  for (let i = 0; i < trianglesPerFace.length; i++) {
    ranges[i] = acc;
    acc += trianglesPerFace[i];
  }
  ranges[trianglesPerFace.length] = acc;
  return ranges;
}

/** Centroid of each face (world-unscaled, in leaf-local coords) for measure snapping. */
export function buildFaceCenters(shape: LeafShape, faceRanges: Uint32Array | undefined): Float32Array | undefined {
  if (!faceRanges) return undefined;
  const tris = shape.triangles;
  const verts = shape.vertices;
  const faceCount = faceRanges.length - 1;
  const centers = new Float32Array(faceCount * 3);
  for (let f = 0; f < faceCount; f++) {
    const triStart = faceRanges[f];
    const triEnd = faceRanges[f + 1];
    let x = 0;
    let y = 0;
    let z = 0;
    let n = 0;
    for (let t = triStart; t < triEnd; t++) {
      for (let k = 0; k < 3; k++) {
        const vi = tris[t * 3 + k] * 3;
        x += verts[vi];
        y += verts[vi + 1];
        z += verts[vi + 2];
        n++;
      }
    }
    if (n > 0) {
      centers[f * 3] = x / n;
      centers[f * 3 + 1] = y / n;
      centers[f * 3 + 2] = z / n;
    }
  }
  return centers;
}
