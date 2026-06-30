import * as THREE from "three";
import { collectTargets, indexFromRanges, type PickTargets } from "./Picker";
import type { LeafObject } from "../core/leaf";
import type { PickEvent, PickMeta } from "../core/types";

export type SelectTopo = "any" | "face" | "edge" | "vertex";

type Cam = THREE.OrthographicCamera | THREE.PerspectiveCamera;
type RaycasterParams = THREE.Raycaster["params"] & { Line2?: { threshold: number } };

const CLICK_PX = 5; // pointer travel below this (with no orbit) counts as a click
const VERT_PX = 8;
const EDGE_PX = 7;

// Pointer -> precise pick. A click (no orbit, little travel) raycasts the right
// target set for the active topology and resolves the exact OCP face/edge/vertex
// index via the prefix-sum back-refs, then emits a PickEvent.
export class InteractionController {
  private enabled = true;
  private topo: SelectTopo = "face";
  private targets: PickTargets = { faces: [], edges: [], vertices: [] };
  private raycaster = new THREE.Raycaster();
  private ndc = new THREE.Vector2();
  private downXY: { x: number; y: number } | null = null;
  private downCam = new THREE.Vector3();

  constructor(
    private dom: HTMLElement,
    private getCamera: () => Cam,
    private emitPick: (p: PickEvent) => void,
  ) {
    dom.addEventListener("pointerdown", this.onDown);
    dom.addEventListener("pointerup", this.onUp);
  }

  setTargets(leaves: LeafObject[]): void {
    this.targets = collectTargets(leaves);
  }
  setEnabled(on: boolean): void {
    this.enabled = on;
  }
  setTopo(t: SelectTopo): void {
    this.topo = t;
  }

  dispose(): void {
    this.dom.removeEventListener("pointerdown", this.onDown);
    this.dom.removeEventListener("pointerup", this.onUp);
  }

  private onDown = (e: PointerEvent): void => {
    if (e.button !== 0) return;
    this.downXY = { x: e.clientX, y: e.clientY };
    this.downCam.copy(this.getCamera().position);
  };

  private onUp = (e: PointerEvent): void => {
    const start = this.downXY;
    this.downXY = null;
    if (!this.enabled || e.button !== 0 || !start) return;
    // Reject if the pointer travelled (a drag) or the camera moved (an orbit).
    const travel = Math.hypot(e.clientX - start.x, e.clientY - start.y);
    if (travel > CLICK_PX) return;
    if (this.getCamera().position.distanceTo(this.downCam) > 1e-6) return;

    const pick = this.resolve(e);
    if (pick) this.emitPick(pick);
  };

  private setNdc(e: PointerEvent): void {
    const r = this.dom.getBoundingClientRect();
    this.ndc.x = ((e.clientX - r.left) / r.width) * 2 - 1;
    this.ndc.y = -((e.clientY - r.top) / r.height) * 2 + 1;
  }

  private worldPerPixel(cam: Cam, heightPx: number): number {
    if (cam instanceof THREE.OrthographicCamera) {
      return (cam.top - cam.bottom) / cam.zoom / Math.max(heightPx, 1);
    }
    const dist = cam.position.length();
    return (2 * Math.tan((cam.fov * Math.PI) / 360) * dist) / Math.max(heightPx, 1);
  }

  private resolve(e: PointerEvent): PickEvent | null {
    const cam = this.getCamera();
    this.setNdc(e);
    this.raycaster.setFromCamera(this.ndc, cam);
    const r = this.dom.getBoundingClientRect();
    const wpp = this.worldPerPixel(cam, r.height);
    this.raycaster.params.Points.threshold = VERT_PX * wpp;
    (this.raycaster.params as RaycasterParams).Line2 = { threshold: EDGE_PX };

    const wantV = this.topo === "vertex" || this.topo === "any";
    const wantE = this.topo === "edge" || this.topo === "any";
    const wantF = this.topo === "face" || this.topo === "any";

    const vHit = wantV ? this.nearest(this.targets.vertices) : null;
    const eHit = wantE ? this.nearest(this.targets.edges) : null;
    const fHit = wantF ? this.nearest(this.targets.faces) : null;

    // Explicit topology: return only that kind.
    if (this.topo === "face") return fHit && this.toFace(fHit);
    if (this.topo === "edge") return eHit && this.toEdge(eHit);
    if (this.topo === "vertex") return vHit && this.toVertex(vHit);

    // "any": snap to the lowest-dimensional entity within a pixel tolerance,
    // else fall back to the face under the cursor.
    if (vHit && this.screenClose(vHit, cam, r.height, VERT_PX)) return this.toVertex(vHit);
    if (eHit && this.screenClose(eHit, cam, r.height, EDGE_PX)) return this.toEdge(eHit);
    if (fHit) return this.toFace(fHit);
    if (eHit) return this.toEdge(eHit);
    return vHit ? this.toVertex(vHit) : null;
  }

  private nearest(objs: THREE.Object3D[]): THREE.Intersection | null {
    if (!objs.length) return null;
    const hits = this.raycaster.intersectObjects(objs, false);
    return hits.length ? hits[0] : null;
  }

  private screenClose(hit: THREE.Intersection, cam: Cam, heightPx: number, px: number): boolean {
    // Compare the cursor ray's closest approach to the hit point against `px`.
    const wpp = this.worldPerPixel(cam, heightPx);
    const onRay = new THREE.Vector3();
    this.raycaster.ray.closestPointToPoint(hit.point, onRay);
    return onRay.distanceTo(hit.point) <= px * wpp;
  }

  private toFace(hit: THREE.Intersection): PickEvent {
    const meta = hit.object.userData.pick as PickMeta;
    const index = indexFromRanges(hit.faceIndex ?? 0, meta.faceRanges);
    return {
      kind: "face",
      shapeId: meta.shapeId,
      index,
      name: meta.name,
      point: hit.point.toArray() as [number, number, number],
      faceNormal: hit.face?.normal?.toArray() as [number, number, number] | undefined,
    };
  }

  private toEdge(hit: THREE.Intersection): PickEvent {
    const meta = hit.object.userData.pick as PickMeta;
    // LineSegments2 reports the hit segment ordinal in `faceIndex`.
    const seg = hit.faceIndex ?? (hit.index != null ? Math.floor(hit.index / 2) : 0);
    const index = indexFromRanges(seg, meta.edgeRanges);
    return {
      kind: "edge",
      shapeId: meta.shapeId,
      index,
      name: meta.name,
      point: hit.point?.toArray() as [number, number, number] | undefined,
    };
  }

  private toVertex(hit: THREE.Intersection): PickEvent {
    const meta = hit.object.userData.pick as PickMeta;
    return {
      kind: "vertex",
      shapeId: meta.shapeId,
      index: hit.index ?? 0,
      name: meta.name,
      point: hit.point?.toArray() as [number, number, number] | undefined,
    };
  }
}
