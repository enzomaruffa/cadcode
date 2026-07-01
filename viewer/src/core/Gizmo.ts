import * as THREE from "three";

// A small RGB orientation gizmo (X=red, Y=green, Z=blue) rendered in the corner,
// reflecting the main camera's orientation. Its own scene + camera, drawn on top
// of the main render each frame.
export class Gizmo {
  private scene = new THREE.Scene();
  private cam: THREE.OrthographicCamera;
  private dir = new THREE.Vector3();
  private sizeVec = new THREE.Vector2();

  constructor() {
    const len = 1.0;
    const mk = (v: THREE.Vector3, color: number) =>
      new THREE.ArrowHelper(v, new THREE.Vector3(), len, color, 0.34, 0.2);
    this.scene.add(mk(new THREE.Vector3(1, 0, 0), 0xe0554e)); // X red
    this.scene.add(mk(new THREE.Vector3(0, 1, 0), 0x6bbf5a)); // Y green
    this.scene.add(mk(new THREE.Vector3(0, 0, 1), 0x5a8fe0)); // Z blue
    this.scene.add(this.label("X", "#e0554e", new THREE.Vector3(1.38, 0, 0)));
    this.scene.add(this.label("Y", "#6bbf5a", new THREE.Vector3(0, 1.38, 0)));
    this.scene.add(this.label("Z", "#5a8fe0", new THREE.Vector3(0, 0, 1.38)));

    this.cam = new THREE.OrthographicCamera(-1.9, 1.9, 1.9, -1.9, 0.1, 20);
    this.cam.up.set(0, 0, 1);
  }

  private label(text: string, color: string, pos: THREE.Vector3): THREE.Sprite {
    const c = document.createElement("canvas");
    c.width = c.height = 64;
    const ctx = c.getContext("2d")!;
    ctx.fillStyle = color;
    ctx.font = "bold 46px system-ui, sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(text, 32, 36);
    const tex = new THREE.CanvasTexture(c);
    tex.colorSpace = THREE.SRGBColorSpace;
    const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, depthTest: false, transparent: true }));
    s.position.copy(pos);
    s.scale.setScalar(0.62);
    return s;
  }

  render(renderer: THREE.WebGLRenderer, mainCamera: THREE.Camera, sizePx = 96): void {
    mainCamera.getWorldDirection(this.dir);
    this.cam.position.copy(this.dir).multiplyScalar(-5);
    this.cam.up.copy(mainCamera.up);
    this.cam.lookAt(0, 0, 0);
    this.cam.updateProjectionMatrix();

    const s = renderer.getSize(this.sizeVec);
    const pad = 10;
    const x = pad;
    const y = s.y - sizePx - pad; // top-left (viewport y is measured from bottom)

    const prevAutoClear = renderer.autoClear;
    renderer.autoClear = false;
    renderer.clearDepth();
    renderer.setScissorTest(true);
    renderer.setScissor(x, y, sizePx, sizePx);
    renderer.setViewport(x, y, sizePx, sizePx);
    renderer.render(this.scene, this.cam);
    renderer.setScissorTest(false);
    renderer.setViewport(0, 0, s.x, s.y);
    renderer.autoClear = prevAutoClear;
  }

  dispose(): void {
    this.scene.traverse((o) => {
      const wm = o as { material?: THREE.Material & { map?: THREE.Texture }; geometry?: THREE.BufferGeometry };
      wm.geometry?.dispose();
      if (wm.material) {
        wm.material.map?.dispose?.();
        wm.material.dispose();
      }
    });
  }
}
