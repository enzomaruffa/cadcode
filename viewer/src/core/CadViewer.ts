import * as THREE from "three";
import type { LineMaterial } from "three/examples/jsm/lines/LineMaterial.js";
import type { NotifyCallback, NotifyChange, RenderPreset, TessShapes, ViewerOptions } from "./types";
import { buildSceneGraph, type SceneGraph } from "./SceneGraph";
import { CameraRig } from "./CameraRig";
import { Controls } from "./Controls";
import { deepDispose } from "./dispose";
import { TECHNICAL } from "../materials/materials";

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

  private notify?: NotifyCallback;
  private sceneGraph: SceneGraph | null = null;
  private preset: RenderPreset = TECHNICAL;
  private resolution = new THREE.Vector2(1, 1);

  private ambient: THREE.AmbientLight;
  private key: THREE.DirectionalLight;

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

    this.ambient = new THREE.AmbientLight(0xffffff, this.preset.ambientIntensity);
    this.key = new THREE.DirectionalLight(0xffffff, this.preset.directIntensity);
    this.scene.add(this.ambient, this.key);

    const r = container.getBoundingClientRect();
    this.resize(Math.max(r.width, 1), Math.max(r.height, 1));
  }

  // --- rendering -------------------------------------------------------------

  render(shapes: TessShapes, preset?: RenderPreset, viewerOptions?: ViewerOptions): void {
    if (this.disposed) return;
    if (preset) this.preset = preset;

    this.clearGraph();
    const sg = buildSceneGraph(shapes, this.preset, this.resolution);
    this.sceneGraph = sg;
    this.scene.add(sg.root);
    this.applyLights();

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
    this.scene.remove(this.sceneGraph.root);
    deepDispose(this.sceneGraph.root);
    this.sceneGraph = null;
  }

  private applyLights(): void {
    this.ambient.intensity = this.preset.ambientIntensity;
    this.key.intensity = this.preset.directIntensity;
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
    // Headlight: light from the eye so reads are clean (B3 replaces with a rig).
    this.key.position.copy(this.rig.camera.position);
    this.renderer.render(this.scene, this.rig.camera);
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
    if (this.sceneGraph) {
      for (const mat of this.sceneGraph.lineMaterials) (mat as LineMaterial).resolution.copy(this.resolution);
    }
    this.requestRender();
  }

  // Picking is wired in B2 (the InteractionController/Picker). Kept as a no-op
  // hook so the React wrapper and call sites are stable.
  setPicking(_enabled: boolean): void {
    void _enabled;
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    if (this.rafId != null) cancelAnimationFrame(this.rafId);
    this.controls.dispose();
    this.clearGraph();
    this.scene.remove(this.ambient, this.key);
    this.ambient.dispose();
    this.key.dispose();
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
