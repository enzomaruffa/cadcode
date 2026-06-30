import * as THREE from "three";

// A soft contact shadow: a ShadowMaterial ground plane (invisible except where
// shadowed, so it composites over the studio gradient) lit by a dedicated
// shadow-casting light whose ortho frustum is fit to the model bbox.
export class ShadowStage {
  readonly light: THREE.DirectionalLight;
  readonly ground: THREE.Mesh;

  constructor(scene: THREE.Scene) {
    this.light = new THREE.DirectionalLight(0xffffff, 0.25);
    this.light.castShadow = true;
    this.light.shadow.mapSize.set(2048, 2048);
    this.light.shadow.bias = -0.0005;
    this.light.shadow.radius = 4;

    const mat = new THREE.ShadowMaterial({ opacity: 0.34 });
    this.ground = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), mat);
    this.ground.receiveShadow = true;
    this.ground.visible = false;
    scene.add(this.light, this.light.target, this.ground);
  }

  setEnabled(on: boolean): void {
    this.ground.visible = on;
    this.light.castShadow = on;
    this.light.intensity = on ? 0.25 : 0;
  }

  // up:Z — PlaneGeometry lies in XY with +Z normal, so it sits under the model.
  fitTo(bbox: THREE.Box3): void {
    const c = bbox.getCenter(new THREE.Vector3());
    const size = bbox.getSize(new THREE.Vector3());
    const r = Math.max(size.length() * 0.5, 1);

    this.ground.position.set(c.x, c.y, bbox.min.z - r * 0.01);
    this.ground.scale.set(r * 4, r * 4, 1);

    this.light.position.set(c.x + r * 0.4, c.y - r * 0.4, bbox.max.z + r * 3);
    this.light.target.position.copy(c);

    const cam = this.light.shadow.camera;
    cam.left = -r * 1.7;
    cam.right = r * 1.7;
    cam.top = r * 1.7;
    cam.bottom = -r * 1.7;
    cam.near = 0.1;
    cam.far = r * 8;
    cam.updateProjectionMatrix();
  }

  dispose(): void {
    this.ground.geometry.dispose();
    (this.ground.material as THREE.Material).dispose();
  }
}
