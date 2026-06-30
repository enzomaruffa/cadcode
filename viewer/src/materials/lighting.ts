import * as THREE from "three";
import { RoomEnvironment } from "three/examples/jsm/environments/RoomEnvironment.js";
import type { RenderPreset } from "../core/types";

// Key (camera headlight) + fill + rim + ambient, plus a generated RoomEnvironment
// IBL (no external HDR, fully offline) for rich PBR reflections. Intensities are
// driven by the active preset; the headlight follows the camera each frame.
export class LightRig {
  readonly ambient: THREE.AmbientLight;
  readonly key: THREE.DirectionalLight;
  readonly fill: THREE.DirectionalLight;
  readonly rim: THREE.DirectionalLight;
  private envTex: THREE.Texture | null = null;

  constructor(scene: THREE.Scene, renderer: THREE.WebGLRenderer) {
    this.ambient = new THREE.AmbientLight(0xfff4e6, 0.6);
    this.key = new THREE.DirectionalLight(0xffffff, 1.4);
    this.fill = new THREE.DirectionalLight(0xcfe0ff, 0.4);
    this.rim = new THREE.DirectionalLight(0xffe9cf, 0.5);
    scene.add(this.ambient, this.key, this.key.target, this.fill, this.fill.target, this.rim, this.rim.target);

    // Generated environment map for IBL — baked once, reused.
    const pmrem = new THREE.PMREMGenerator(renderer);
    this.envTex = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
    scene.environment = this.envTex;
    pmrem.dispose();
  }

  applyPreset(preset: RenderPreset): void {
    // IBL contributes ambient, so trim the AmbientLight to avoid washing out.
    this.ambient.intensity = preset.ambientIntensity * 0.55;
    this.key.intensity = preset.directIntensity;
    this.fill.intensity = preset.directIntensity * 0.28;
    this.rim.intensity = preset.directIntensity * (preset.shadowEnabled ? 0.55 : 0.35);
  }

  /** Headlight follows the eye for clean reads; called each frame. */
  updateHeadlight(camera: THREE.Camera): void {
    this.key.position.copy(camera.position);
  }

  /** Place fill/rim relative to the model bbox (static, world-anchored). */
  positionTo(bbox: THREE.Box3): void {
    const c = bbox.getCenter(new THREE.Vector3());
    const r = Math.max(bbox.getSize(new THREE.Vector3()).length() * 0.5, 1);
    this.key.target.position.copy(c);
    this.fill.position.set(c.x - r * 1.5, c.y - r * 2, c.z + r);
    this.fill.target.position.copy(c);
    this.rim.position.set(c.x - r * 0.5, c.y + r * 2, c.z + r * 2.5);
    this.rim.target.position.copy(c);
  }

  dispose(): void {
    this.envTex?.dispose();
    this.envTex = null;
  }
}
