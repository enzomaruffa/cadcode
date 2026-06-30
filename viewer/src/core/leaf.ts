import * as THREE from "three";
import { LineSegmentsGeometry } from "three/examples/jsm/lines/LineSegmentsGeometry.js";
import { LineSegments2 } from "three/examples/jsm/lines/LineSegments2.js";
import { LineMaterial } from "three/examples/jsm/lines/LineMaterial.js";
import type { RenderPreset, TessPart, PickMeta } from "./types";
import { makeLeafMaterials } from "../materials/materials";
import { buildMeshGeometry, buildFaceRanges, buildFaceCenters, flattenNumbers } from "./buildGeometry";

export interface LeafObject {
  group: THREE.Group;
  front?: THREE.Mesh;
  back?: THREE.Mesh;
  edges?: LineSegments2;
  points?: THREE.Points;
  pickMeta: PickMeta;
}

export interface LeafBuildOpts {
  preset: RenderPreset;
  resolution: THREE.Vector2;
  faceOrdinal: number;
}

const pathName = (id: string) => id.replaceAll("/", "|");

function buildEdges(edges: unknown, color: THREE.ColorRepresentation, resolution: THREE.Vector2): LineSegments2 {
  const geom = new LineSegmentsGeometry();
  // `edges` is a list of segments [[x,y,z],[x,y,z]] — flatten to [x,y,z,x,y,z,...].
  geom.setPositions(new Float32Array(flattenNumbers(edges)));
  const mat = new LineMaterial({
    color: new THREE.Color(color).getHex(),
    linewidth: 1.4, // screen pixels
    worldUnits: false,
    transparent: true,
    depthTest: true,
  });
  mat.resolution.copy(resolution);
  const seg = new LineSegments2(geom, mat);
  seg.renderOrder = 999;
  // Edges are part of the model; skip frustum culling so a stray NaN bounding
  // sphere can never hide them (computeLineDistances is for dashed lines only).
  seg.frustumCulled = false;
  return seg;
}

// Build one leaf (solid/face mesh, edges, vertex points) into a group, carrying
// the pick back-refs needed for precise per-face/edge/vertex resolution later.
export function buildLeaf(part: TessPart, opts: LeafBuildOpts): LeafObject {
  const { preset, resolution, faceOrdinal } = opts;
  const group = new THREE.Group();
  group.name = pathName(part.id);

  const state = part.state ?? [1, 1];
  const color = part.color;
  const alpha = part.alpha ?? 1;
  const shape = part.shape;

  const faceRanges = buildFaceRanges(shape?.triangles_per_face);
  const edgeRanges = buildFaceRanges(shape?.segments_per_edge);
  const pickMeta: PickMeta = {
    shapeId: part.id,
    kind: part.type === "edges" ? "edge" : part.type === "vertices" ? "vertex" : "face",
    trianglesPerFace: shape?.triangles_per_face,
    segmentsPerEdge: shape?.segments_per_edge,
    faceRanges,
    edgeRanges,
    faceCenters: shape && faceRanges ? buildFaceCenters(shape, faceRanges) : undefined,
    faceOrdinal,
    name: pathName(part.id),
  };
  group.userData.pick = pickMeta;

  const leaf: LeafObject = { group, pickMeta };
  if (!shape) return leaf;

  // Faces (solid or face leaf) -> front + back mesh sharing one geometry.
  if (part.type !== "edges" && part.type !== "vertices" && shape.vertices?.length) {
    const geom = buildMeshGeometry(shape);
    const { front, back } = makeLeafMaterials(color, alpha, preset);
    const backMesh = new THREE.Mesh(geom, back);
    const frontMesh = new THREE.Mesh(geom, front);
    backMesh.name = group.name;
    frontMesh.name = group.name;
    frontMesh.userData.pick = pickMeta;
    backMesh.userData.pick = pickMeta;
    frontMesh.visible = state[0] === 1;
    backMesh.visible = state[0] === 1;
    frontMesh.castShadow = true;
    frontMesh.receiveShadow = true;
    if (alpha < 1) {
      // Transparent leaves (e.g. the geomdiff ghost) draw after opaque geometry.
      frontMesh.renderOrder = 999;
      backMesh.renderOrder = 998;
    }
    group.add(backMesh, frontMesh);
    leaf.front = frontMesh;
    leaf.back = backMesh;
    group.userData.frontGeometry = geom;
  }

  // Edges.
  if (shape.edges?.length) {
    const edges = buildEdges(shape.edges, preset.edgeColor, resolution);
    edges.name = group.name;
    edges.userData.pick = { ...pickMeta, kind: "edge" } satisfies PickMeta;
    edges.visible = state[1] === 1;
    group.add(edges);
    leaf.edges = edges;
  }

  // Vertex points (built for picking; not drawn unless this is a vertices leaf).
  if (shape.obj_vertices?.length) {
    const pgeom = new THREE.BufferGeometry();
    pgeom.setAttribute("position", new THREE.BufferAttribute(new Float32Array(flattenNumbers(shape.obj_vertices)), 3));
    const pmat = new THREE.PointsMaterial({
      color: new THREE.Color(preset.edgeColor).getHex(),
      size: 5,
      sizeAttenuation: false,
    });
    const points = new THREE.Points(pgeom, pmat);
    points.name = group.name;
    points.userData.pick = { ...pickMeta, kind: "vertex" } satisfies PickMeta;
    points.visible = part.type === "vertices";
    group.add(points);
    leaf.points = points;
  }

  return leaf;
}
