import * as THREE from "three";
import type { LineMaterial } from "three/examples/jsm/lines/LineMaterial.js";
import type { NotifyCallback, NotifyChange, PickEvent, RenderPreset, TessShapes, ViewerOptions } from "./types";
import { buildSceneGraph, type SceneGraph } from "./SceneGraph";
import { CameraRig } from "./CameraRig";
import { Controls } from "./Controls";
import { Grid, type GridMode } from "./Grid";
import { Gizmo } from "./Gizmo";
import { deepDispose } from "./dispose";
import { TECHNICAL } from "../materials/materials";
import { LightRig } from "../materials/lighting";
import { ShadowStage } from "../materials/shadows";
import { Post } from "../materials/postprocessing";
import { applyHighlight } from "../materials/colorApi";
import { InteractionController, type SelectTopo } from "../interaction/InteractionController";
import { SelectionHighlight } from "../interaction/SelectionHighlight";
import { Section, type SectionAxis } from "../interaction/Section";
import { Measure } from "../interaction/Measure";
import { PhysicalOverlay, type PhysicalData } from "../interaction/PhysicalOverlay";
import { PhysicsClient } from "../physics/PhysicsClient";
import type { RawJoint } from "../physics/protocol";

export type InteractionMode = "select" | "measure";

const DPR_CAP = 2;

// The framework-agnostic renderer. Owns the WebGLRenderer, scene, camera rig,
// controls and an on-demand render loop. Rebuilds the scene graph on each
// render() (disposing the old one) and preserves the camera across rebuilds.
export class CadViewer {
  readonly container: HTMLElement;
  readonly renderer: THREE.WebGLRenderer;
  readonly scene: THREE.Scene;
  readonly rig: CameraRig;
  readonly controls: Controls;
  readonly controller: InteractionController;

  private notify?: NotifyCallback;
  private sceneGraph: SceneGraph | null = null;
  private preset: RenderPreset = TECHNICAL;
  private resolution = new THREE.Vector2(1, 1);

  private lights: LightRig;
  private shadow: ShadowStage;
  private post: Post;
  private selection: SelectionHighlight;
  private section: Section;
  private measure: Measure;
  private physical: PhysicalOverlay;
  private grid: Grid;
  private gizmo: Gizmo;
  private physics: PhysicsClient;
  private joints: RawJoint[] = []; // assembly joint graph for the articulated sim
  private explodeOrig: Map<object, THREE.Vector3> | null = null; // per-leaf home positions
  private mode: InteractionMode = "select";

  private framedOnce = false;
  private rafId: number | null = null;
  private disposed = false;
  // Motion sim: leaves currently flashed for collision → their original color.
  private flashed = new Map<string, THREE.Color>();

  // Camera-change diff bookkeeping for the notify callback.
  private last: { position?: number[]; quaternion?: number[]; zoom?: number; target?: number[] } = {};

  constructor(container: HTMLElement, options: ViewerOptions, notify?: NotifyCallback) {
    this.container = container;
    this.notify = notify;

    this.renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true, stencil: true });
    this.renderer.setClearColor(0x000000, 0);
    this.renderer.localClippingEnabled = true;
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    container.appendChild(this.renderer.domElement);
    this.renderer.domElement.style.display = "block";
    this.renderer.domElement.style.width = "100%";
    this.renderer.domElement.style.height = "100%";

    this.scene = new THREE.Scene();

    this.rig = new CameraRig(options);
    this.controls = new Controls(this.rig.camera, this.renderer.domElement, this.rig.target, () => {
      this.emitCameraChange();
      this.requestRender();
    });

    this.lights = new LightRig(this.scene, this.renderer);
    this.lights.applyPreset(this.preset);
    this.shadow = new ShadowStage(this.scene);
    this.selection = new SelectionHighlight(this.scene, this.resolution);
    this.section = new Section(this.scene);
    this.measure = new Measure(this.scene, container);
    this.physical = new PhysicalOverlay(this.scene, container);
    this.grid = new Grid(this.scene);
    this.gizmo = new Gizmo();
    this.physics = new PhysicsClient(
      this.renderer.domElement,
      () => this.rig.camera,
      (enabled) => (this.controls.controls.enabled = enabled),
      () => this.requestRender(),
    );

    this.controller = new InteractionController(
      this.renderer.domElement,
      () => this.rig.camera,
      (p) => this.onPick(p),
    );

    const r = container.getBoundingClientRect();
    const w = Math.max(Math.floor(r.width), 1);
    const h = Math.max(Math.floor(r.height), 1);
    this.post = new Post(this.renderer, this.scene, this.rig.camera, w, h);
    this.resize(w, h);
  }

  // --- rendering -------------------------------------------------------------

  render(shapes: TessShapes, preset?: RenderPreset, viewerOptions?: ViewerOptions): void {
    if (this.disposed) return;
    if (preset) this.preset = preset;

    // A rebuild replaces the leaves the physics bodies point at — stop the sim.
    if (this.physics.active) this.stopPhysics();
    this.explodeOrig = null; // fresh leaves → re-capture explode home positions
    this.clearGraph();
    const sg = buildSceneGraph(shapes, this.preset, this.resolution);
    this.sceneGraph = sg;
    this.scene.add(sg.root);
    this.applyPreset(sg.bbox);
    this.section.fitTo(sg.bbox);
    this.applyClipping();
    this.grid.fitTo(sg.bbox);
    this.controller.setTargets(sg.leaves);

    // The camera rig is long-lived, so it persists across geometry rebuilds for
    // free. Only fit on the very first render, or restore when the host hands us
    // an explicit camera (e.g. switching documents).
    if (!this.framedOnce) {
      this.rig.fitTo(sg.bbox);
      this.controls.syncTarget(this.rig.target);
      this.framedOnce = true;
    } else if (viewerOptions?.position) {
      this.rig.restore(viewerOptions);
      this.controls.syncTarget(this.rig.target);
    }
    this.requestRender();
  }

  /** Remove + dispose the current scene graph, keep renderer/camera/controls. */
  clear(): void {
    this.clearGraph();
    this.requestRender();
  }

  private clearGraph(): void {
    if (!this.sceneGraph) return;
    // Clear the selection overlay BEFORE disposing the geometry it references.
    this.selection.clear();
    this.scene.remove(this.sceneGraph.root);
    deepDispose(this.sceneGraph.root);
    this.sceneGraph = null;
    // A rebuild resets poses + materials, so drop the flash bookkeeping.
    this.flashed.clear();
  }

  // A pick either feeds the measure tool or highlights + notifies the host.
  private onPick(p: PickEvent): void {
    if (this.mode === "measure") {
      if (p.point) this.measure.addPoint(p.point);
      this.requestRender();
      return;
    }
    const leaf = this.sceneGraph?.leafById.get(p.shapeId);
    if (leaf) {
      if (p.kind === "edge") this.selection.showEdge(leaf, p.index);
      else if (p.kind === "face") this.selection.showFace(leaf, p.index);
    }
    this.requestRender();
    this.notify?.({ pick: { new: p } });
  }

  // Apply the active preset to lights, shadow, AO and tone mapping (no rebuild).
  private applyPreset(bbox: THREE.Box3): void {
    this.lights.applyPreset(this.preset);
    this.lights.positionTo(bbox);
    this.shadow.setEnabled(!!this.preset.shadowEnabled);
    this.shadow.fitTo(bbox);
    this.post.configure(this.preset, bbox);
    this.renderer.toneMapping = this.preset.toneMapping ? THREE.ACESFilmicToneMapping : THREE.NoToneMapping;
    this.renderer.toneMappingExposure = 1;
  }

  private requestRender(): void {
    if (this.disposed || this.rafId != null) return;
    this.rafId = requestAnimationFrame(() => {
      this.rafId = null;
      this.draw();
    });
  }

  private draw(): void {
    if (this.disposed) return;
    this.lights.updateHeadlight(this.rig.camera);
    // Presentation goes through the AO/AA composer; technical renders straight
    // (crisp, literal colors). Diagnostic modes keep AO off via their presets.
    if (this.preset.aoEnabled) this.post.render();
    else this.renderer.render(this.scene, this.rig.camera);
    this.measure.render(this.scene, this.rig.camera);
    this.physical.render(this.scene, this.rig.camera);
    this.gizmo.render(this.renderer, this.rig.camera);
  }

  // --- camera ---------------------------------------------------------------

  fit(): void {
    if (this.sceneGraph) {
      this.rig.fitTo(this.sceneGraph.bbox);
      this.controls.syncTarget(this.rig.target);
      this.emitCameraChange();
      this.requestRender();
    }
  }

  setOrtho(ortho: boolean): void {
    if (this.rig.isOrtho === ortho) return;
    this.rig.isOrtho = ortho;
    this.controls.setCamera(this.rig.camera, this.renderer.domElement);
    this.post.renderPass.camera = this.rig.camera;
    this.post.gtao.camera = this.rig.camera;
    this.requestRender();
  }

  getCameraState(): Required<Pick<ViewerOptions, "position" | "quaternion" | "target" | "zoom">> {
    return this.rig.getState();
  }

  setCameraState(opts: ViewerOptions): void {
    this.rig.restore(opts);
    this.controls.syncTarget(this.rig.target);
    this.emitCameraChange();
    this.requestRender();
  }

  private emitCameraChange(): void {
    if (!this.notify) return;
    const s = this.rig.getState();
    const change: NotifyChange = {};
    if (!arrEq(s.position, this.last.position)) change.position = { new: s.position };
    if (!arrEq(s.quaternion, this.last.quaternion)) change.quaternion = { new: s.quaternion };
    if (!arrEq(s.target, this.last.target)) change.target = { new: s.target };
    if (s.zoom !== this.last.zoom) change.zoom = { new: s.zoom };
    this.last = s;
    if (Object.keys(change).length) this.notify(change);
  }

  // --- resize / picking / lifecycle -----------------------------------------

  resize(width: number, height: number): void {
    if (this.disposed) return;
    const dpr = Math.min(window.devicePixelRatio || 1, DPR_CAP);
    this.renderer.setPixelRatio(dpr);
    this.renderer.setSize(width, height, false);
    this.rig.setAspect(width, height);
    this.resolution.set(width * dpr, height * dpr);
    this.post?.setSize(width, height);
    this.measure?.setSize(width, height);
    this.physical?.setSize(width, height);
    if (this.sceneGraph) {
      for (const mat of this.sceneGraph.lineMaterials) (mat as LineMaterial).resolution.copy(this.resolution);
    }
    this.requestRender();
  }

  setPicking(enabled: boolean): void {
    this.controller.setEnabled(enabled);
  }

  setSelectTopo(topo: SelectTopo): void {
    this.controller.setTopo(topo);
  }

  /** Section/clipping plane along an axis (offset 0..1), or null to disable. */
  setSection(axis: SectionAxis | null, offset = 0.5): void {
    if (axis) this.section.set(axis, offset);
    else this.section.disable();
    this.applyClipping();
    this.requestRender();
  }

  setInteractionMode(mode: InteractionMode): void {
    this.mode = mode;
  }

  clearMeasure(): void {
    this.measure.clear();
    this.requestRender();
  }

  setGrid(on: boolean | GridMode): void {
    this.grid.setEnabled(on);
    this.requestRender();
  }

  /** Model bbox size [w, d, h] in mm — drives the size chip in the toolbar. */
  getModelSize(): [number, number, number] | null {
    const bb = this.sceneGraph?.bbox;
    if (!bb) return null;
    const s = bb.getSize(new THREE.Vector3());
    return [s.x, s.y, s.z];
  }

  private applyClipping(): void {
    if (!this.sceneGraph) return;
    const planes = this.section.planes;
    for (const leaf of this.sceneGraph.leaves) {
      if (leaf.front) (leaf.front.material as THREE.Material).clippingPlanes = planes;
      if (leaf.back) (leaf.back.material as THREE.Material).clippingPlanes = planes;
    }
  }

  /** Cheap highlight-mode recolor (glow the active line's faces) — no rebuild. */
  recolorHighlight(activeLine: number | null): void {
    if (this.sceneGraph) {
      applyHighlight(this.sceneGraph, activeLine);
      this.requestRender();
    }
  }

  /** Physical-properties overlay (COM marker + support polygon), or null to clear. */
  setPhysical(data: PhysicalData | null): void {
    if (data) this.physical.set(data);
    else this.physical.clear();
    this.requestRender();
  }

  /** Motion sim: place each moving leaf at an absolute pose (position + quaternion,
   * three.js order). Overwrites the baked pose — the model is tessellated once and
   * only its rigid transforms animate. */
  setPose(poses: Record<string, [[number, number, number], [number, number, number, number]]>): void {
    if (!this.sceneGraph) return;
    for (const [id, loc] of Object.entries(poses)) {
      const leaf = this.sceneGraph.leafById.get(id);
      if (!leaf) continue;
      leaf.group.position.set(loc[0][0], loc[0][1], loc[0][2]);
      leaf.group.quaternion.set(loc[1][0], loc[1][1], loc[1][2], loc[1][3]);
    }
    this.requestRender();
  }

  /** Motion sim: flash the given leaves (colliding parts) a color, restoring any
   * previously-flashed leaf no longer in the set. Cheap material-color mutation. */
  flashLeaves(ids: string[], hex: string): void {
    if (!this.sceneGraph) return;
    const want = new Set(ids);
    for (const [id, orig] of [...this.flashed]) {
      if (!want.has(id)) {
        const leaf = this.sceneGraph.leafById.get(id);
        if (leaf?.front) (leaf.front.material as THREE.MeshStandardMaterial).color.copy(orig);
        this.flashed.delete(id);
      }
    }
    for (const id of ids) {
      const leaf = this.sceneGraph.leafById.get(id);
      if (!leaf?.front) continue;
      const mat = leaf.front.material as THREE.MeshStandardMaterial;
      if (!this.flashed.has(id)) this.flashed.set(id, mat.color.clone());
      mat.color.set(hex);
    }
    this.requestRender();
  }

  /** Interactive physics playground: parts fall under gravity onto a floor and
   * can be grabbed + dragged (a constraint pulls the grabbed part toward the
   * pointer, so it pushes and is blocked by the others). Ephemeral — stopping
   * restores the code-truth poses. Runs a continuous loop while active. */
  startPhysics(): void {
    if (this.disposed || !this.sceneGraph || this.physics.active) return;
    this.selection.clear();
    if (this.explodeOrig) this.setExplode(0); // physics starts from the assembled pose
    this.setPicking(false); // the physics grab owns the pointer while playing
    // The solver runs in a Web Worker and streams transforms back; it drives the
    // render itself (via the requestRender callback) — no main-thread step loop.
    this.physics.start(this.sceneGraph, this.joints);
  }

  /** Assembly joint graph for the next physics run (revolute/prismatic/… →
   *  articulated constraints). Set from the geometry payload before play. */
  setJoints(joints: RawJoint[]): void {
    this.joints = joints;
  }

  /** Exploded view: slide each part radially out from the assembly center by
   *  `factor` (0 = assembled, 1 = full spread). Reversible; reset on rebuild. */
  setExplode(factor: number): void {
    const sg = this.sceneGraph;
    if (!sg) return;
    if (!this.explodeOrig) {
      this.explodeOrig = new Map();
      for (const leaf of sg.leaves) this.explodeOrig.set(leaf, leaf.group.position.clone());
    }
    const center = new THREE.Vector3();
    sg.bbox.getCenter(center);
    for (const leaf of sg.leaves) {
      const orig = this.explodeOrig.get(leaf);
      if (!orig) continue;
      leaf.group.position.copy(orig).addScaledVector(orig.clone().sub(center), factor);
    }
    this.requestRender();
  }

  stopPhysics(): void {
    if (this.physics.active) this.physics.stop();
    if (!this.disposed) this.setPicking(true);
    this.requestRender();
  }

  resetPhysics(): void {
    this.physics.reset();
  }

  /** Per-part pose deltas (code → current sim pose) for baking back to code.
   *  Call while physics is active; empty otherwise. */
  bakePhysics(): Record<string, { pos: [number, number, number]; axis: [number, number, number]; angle: number }> {
    return this.physics.active ? this.physics.bakePoses() : {};
  }

  get physicsActive(): boolean {
    return this.physics.active;
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    if (this.rafId != null) cancelAnimationFrame(this.rafId);
    this.physics.dispose();
    this.controller.dispose();
    this.controls.dispose();
    this.clearGraph();
    this.lights.dispose();
    this.shadow.dispose();
    this.selection.dispose();
    this.section.dispose();
    this.measure.dispose();
    this.physical.dispose();
    this.grid.dispose();
    this.gizmo.dispose();
    this.post.dispose();
    this.renderer.renderLists.dispose();
    this.renderer.dispose();
    this.renderer.forceContextLoss();
    if (this.renderer.domElement.parentNode === this.container) {
      this.container.removeChild(this.renderer.domElement);
    }
  }
}

function arrEq(a: number[] | undefined, b: number[] | undefined): boolean {
  if (!a || !b || a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) if (Math.abs(a[i] - b[i]) > 1e-9) return false;
  return true;
}
