import * as THREE from "three";
import { LineMaterial } from "three/examples/jsm/lines/LineMaterial.js";
import type { RenderPreset, TessShapes, TessPart, Loc } from "./types";
import { buildLeaf, type LeafObject } from "./leaf";

export interface SceneGraph {
  root: THREE.Group;
  groups: Map<string, THREE.Object3D>;
  leaves: LeafObject[];
  leafById: Map<string, LeafObject>;
  bbox: THREE.Box3;
  lineMaterials: LineMaterial[];
}

const IDENTITY: Loc = [
  [0, 0, 0],
  [0, 0, 0, 1],
];

function applyLoc(obj: THREE.Object3D, loc: Loc | null | undefined): void {
  const l = loc ?? IDENTITY;
  obj.position.set(l[0][0], l[0][1], l[0][2]);
  obj.quaternion.set(l[1][0], l[1][1], l[1][2], l[1][3]);
}

interface Ctx {
  preset: RenderPreset;
  resolution: THREE.Vector2;
  groups: Map<string, THREE.Object3D>;
  leaves: LeafObject[];
  leafById: Map<string, LeafObject>;
  lineMaterials: LineMaterial[];
  faceOrdinal: { n: number };
}

function buildNode(node: TessShapes | TessPart, ctx: Ctx): THREE.Group {
  // A node with a `parts` array is a container; otherwise it's a leaf.
  const asPart = node as TessPart;
  if (Array.isArray(asPart.parts)) {
    const group = new THREE.Group();
    group.name = node.id.replaceAll("/", "|");
    applyLoc(group, node.loc as Loc | null);
    ctx.groups.set(node.id, group);
    for (const child of asPart.parts) group.add(buildNode(child, ctx));
    return group;
  }

  // Leaf.
  const leaf = buildLeaf(asPart, {
    preset: ctx.preset,
    resolution: ctx.resolution,
    faceOrdinal: ctx.faceOrdinal.n++,
  });
  applyLoc(leaf.group, asPart.loc);
  ctx.groups.set(asPart.id, leaf.group);
  ctx.leaves.push(leaf);
  ctx.leafById.set(asPart.id, leaf);
  if (leaf.edges) ctx.lineMaterials.push(leaf.edges.material as LineMaterial);
  return leaf.group;
}

// Recursively turn the tessellation tree into a three.js scene graph and report
// the group lookup map, leaf list, bbox, and the line materials (whose
// `resolution` must track canvas size on resize).
export function buildSceneGraph(shapes: TessShapes, preset: RenderPreset, resolution: THREE.Vector2): SceneGraph {
  const ctx: Ctx = {
    preset,
    resolution,
    groups: new Map(),
    leaves: [],
    leafById: new Map(),
    lineMaterials: [],
    faceOrdinal: { n: 0 },
  };
  const root = buildNode(shapes, ctx);

  let bbox: THREE.Box3;
  if (shapes.bb) {
    bbox = new THREE.Box3(
      new THREE.Vector3(shapes.bb.xmin, shapes.bb.ymin, shapes.bb.zmin),
      new THREE.Vector3(shapes.bb.xmax, shapes.bb.ymax, shapes.bb.zmax),
    );
  } else {
    bbox = new THREE.Box3().setFromObject(root);
  }
  if (bbox.isEmpty()) {
    bbox = new THREE.Box3(new THREE.Vector3(-1, -1, -1), new THREE.Vector3(1, 1, 1));
  }

  return {
    root,
    groups: ctx.groups,
    leaves: ctx.leaves,
    leafById: ctx.leafById,
    bbox,
    lineMaterials: ctx.lineMaterials,
  };
}
