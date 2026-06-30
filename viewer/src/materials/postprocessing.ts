import * as THREE from "three";
import { EffectComposer } from "three/examples/jsm/postprocessing/EffectComposer.js";
import { RenderPass } from "three/examples/jsm/postprocessing/RenderPass.js";
import { GTAOPass } from "three/examples/jsm/postprocessing/GTAOPass.js";
import { SMAAPass } from "three/examples/jsm/postprocessing/SMAAPass.js";
import { OutputPass } from "three/examples/jsm/postprocessing/OutputPass.js";
import type { RenderPreset } from "../core/types";

type Cam = THREE.OrthographicCamera | THREE.PerspectiveCamera;

// Presentation pipeline: RenderPass -> GTAO (ground-truth AO, ortho-aware) ->
// SMAA -> OutputPass. Built once; passes toggle by preset. The OutlinePass hook
// (B5) is inserted before SMAA. Render targets keep alpha so the transparent
// canvas still composites over the CSS studio gradient.
export class Post {
  readonly composer: EffectComposer;
  readonly renderPass: RenderPass;
  readonly gtao: GTAOPass;
  readonly smaa: SMAAPass;
  readonly output: OutputPass;

  constructor(renderer: THREE.WebGLRenderer, scene: THREE.Scene, camera: Cam, width: number, height: number) {
    const rt = new THREE.WebGLRenderTarget(width, height, {
      type: THREE.HalfFloatType,
      samples: 2,
    });
    this.composer = new EffectComposer(renderer, rt);
    this.composer.setSize(width, height);

    this.renderPass = new RenderPass(scene, camera);
    this.renderPass.clearAlpha = 0;
    this.gtao = new GTAOPass(scene, camera, width, height);
    this.gtao.output = GTAOPass.OUTPUT.Default;
    this.smaa = new SMAAPass();
    this.output = new OutputPass();

    this.composer.addPass(this.renderPass);
    this.composer.addPass(this.gtao);
    this.composer.addPass(this.smaa);
    this.composer.addPass(this.output);
  }

  setSize(width: number, height: number): void {
    this.composer.setSize(width, height);
    this.gtao.setSize(width, height);
  }

  configure(preset: RenderPreset, bbox: THREE.Box3): void {
    const on = !!preset.aoEnabled;
    this.gtao.enabled = on;
    this.smaa.enabled = on;
    if (on) {
      const r = Math.max(bbox.getSize(new THREE.Vector3()).length() * 0.5, 1);
      this.gtao.updateGtaoMaterial({
        radius: r * 0.12,
        distanceExponent: 1,
        thickness: r * 0.5,
        scale: 1,
        samples: 16,
        screenSpaceRadius: false,
      });
      this.gtao.updatePdMaterial({
        lumaPhi: 10,
        depthPhi: 2,
        normalPhi: 3,
        radius: 4,
        radiusExponent: 1,
        rings: 2,
        samples: 16,
      });
    }
  }

  render(): void {
    this.composer.render();
  }

  dispose(): void {
    this.composer.dispose();
    this.gtao.dispose();
  }
}
