import * as THREE from "three";
import type { LineMaterial } from "three/examples/jsm/lines/LineMaterial.js";
import type { NotifyCallback, NotifyChange, PickEvent, RenderPreset, TessShapes, ViewerOptions } from "./types";
import { buildSceneGraph, type SceneGraph } from "./SceneGraph";
import { CameraRig } from "./CameraRig";
import { Controls } from "./Controls";
import { Grid } from "./Grid";
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
  private grid: Grid;
  private gizmo: Gizmo;
  private mode: InteractionMode = "select";

  private framedOnce = false;
  private rafId: number | null = null;
  private disposed = false;

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
    this.grid = new Grid(this.scene);
    this.gizmo = new Gizmo();

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

  setGrid(on: boolean): void {
    this.grid.setEnabled(on);
    this.requestRender();
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

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    if (this.rafId != null) cancelAnimationFrame(this.rafId);
    this.controller.dispose();
    this.controls.dispose();
    this.clearGraph();
    this.lights.dispose();
    this.shadow.dispose();
    this.selection.dispose();
    this.section.dispose();
    this.measure.dispose();
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
