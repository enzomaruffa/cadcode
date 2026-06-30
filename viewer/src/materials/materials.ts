import * as THREE from "three";
import type { RenderPreset } from "../core/types";

// The three legacy looks, carried forward verbatim (+ new IBL/AO/shadow knobs).
export const TECHNICAL: RenderPreset = {
  ambientIntensity: 0.9,
  directIntensity: 1.45,
  metalness: 0.42,
  roughness: 0.5,
  edgeColor: 0x6b5a3a,
  defaultOpacity: 0.5,
  normalLen: 0,
  envIntensity: 0.35,
  aoEnabled: false,
  shadowEnabled: false,
  toneMapping: false,
};

export const PRESENTATION: RenderPreset = {
  ambientIntensity: 1.3,
  directIntensity: 2.2,
  metalness: 0.55,
  roughness: 0.35,
  edgeColor: 0x4a3d28,
  defaultOpacity: 0.5,
  normalLen: 0,
  envIntensity: 1.0,
  aoEnabled: true,
  shadowEnabled: true,
  toneMapping: true,
};

export const PREVIEW: RenderPreset = {
  ambientIntensity: 1.1,
  directIntensity: 1.6,
  metalness: 0.45,
  roughness: 0.45,
  edgeColor: 0x6b5a3a,
  defaultOpacity: 0.5,
  normalLen: 0,
  envIntensity: 0.9,
  aoEnabled: false,
  shadowEnabled: true,
  toneMapping: true,
};

// Warm default when the source doesn't specify a color — matches the UI palette.
export const DEFAULT_COLOR = "#c9a06a";

export interface LeafMaterials {
  front: THREE.MeshStandardMaterial;
  back: THREE.MeshBasicMaterial;
}

function toColor(c: string | number | undefined): THREE.Color {
  if (c == null) return new THREE.Color(DEFAULT_COLOR);
  return new THREE.Color(c as THREE.ColorRepresentation);
}

/**
 * Front (PBR) + back (silhouette fill) materials for one solid/face leaf.
 * Transparency mirrors the proven three-cad-viewer model so the geomdiff ghost
 * (alpha 0.12) reads correctly behind opaque added/removed material.
 */
export function makeLeafMaterials(
  color: string | number | undefined,
  alpha: number,
  preset: RenderPreset,
): LeafMaterials {
  const col = toColor(color);
  const transparent = alpha < 1;

  const front = new THREE.MeshStandardMaterial({
    color: col,
    metalness: preset.metalness,
    roughness: preset.roughness,
    envMapIntensity: preset.envIntensity ?? 1,
    side: THREE.FrontSide,
    flatShading: false,
    polygonOffset: true,
    polygonOffsetFactor: 1,
    polygonOffsetUnits: 1,
    transparent,
    opacity: alpha,
    depthWrite: !transparent,
  });

  const back = new THREE.MeshBasicMaterial({
    color: col,
    side: THREE.BackSide,
    transparent,
    opacity: alpha,
    depthWrite: !transparent,
  });

  return { front, back };
}
