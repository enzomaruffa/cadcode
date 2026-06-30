import * as THREE from "three";

// Walk an Object3D subtree and release every GPU resource it owns. Geometries
// shared between sibling meshes (front+back share one) are de-duped so we never
// double-dispose. Called on every re-render (clear) and on teardown.
export function deepDispose(root: THREE.Object3D): void {
  const geometries = new Set<THREE.BufferGeometry>();
  const materials = new Set<THREE.Material>();
  const textures = new Set<THREE.Texture>();

  root.traverse((obj) => {
    const withGeom = obj as { geometry?: THREE.BufferGeometry };
    if (withGeom.geometry) geometries.add(withGeom.geometry);

    const withMat = obj as { material?: THREE.Material | THREE.Material[] };
    if (withMat.material) {
      const mats = Array.isArray(withMat.material) ? withMat.material : [withMat.material];
      for (const m of mats) materials.add(m);
    }
  });

  for (const m of materials) {
    // Collect any textures referenced by the material before disposing it.
    for (const value of Object.values(m as unknown as Record<string, unknown>)) {
      if (value instanceof THREE.Texture) textures.add(value);
    }
    m.dispose();
  }
  for (const g of geometries) g.dispose();
  for (const t of textures) t.dispose();
}
