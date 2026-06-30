import { useEffect, useRef } from "react";
import { Display, Viewer } from "three-cad-viewer";
import "three-cad-viewer/dist/three-cad-viewer.css";
import { HTTP_URL } from "../config";

const RENDER = {
  ambientIntensity: 1.1,
  directIntensity: 1.6,
  metalness: 0.45,
  roughness: 0.45,
  edgeColor: 0x707070,
  defaultOpacity: 0.5,
  normalLen: 0,
};

// A standalone, orbitable mini three-cad-viewer for one library part.
export function PartPreview({ name }: { name: string }) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const container = ref.current;
    if (!container) return;
    let viewer: Viewer | null = null;
    let disposed = false;

    fetch(`${HTTP_URL}/library/${name}/geometry`)
      .then((r) => r.json())
      .then((d: { shapes?: unknown; error?: string }) => {
        if (disposed || !d.shapes || d.error) return;
        const w = Math.max(container.clientWidth, 360);
        const h = Math.max(container.clientHeight, 320);
        const display = new Display(container, {
          cadWidth: w,
          height: h,
          treeWidth: 0,
          theme: "dark",
          glass: true,
          pinning: false,
        });
        viewer = new Viewer(display, { up: "Z", control: "trackball", ortho: true }, () => {});
        viewer.render(d.shapes, RENDER, { up: "Z", control: "trackball", ortho: true });
      })
      .catch(() => void 0);

    return () => {
      disposed = true;
      try {
        (viewer as unknown as { dispose?: () => void } | null)?.dispose?.();
      } catch {
        /* ignore */
      }
      container.innerHTML = "";
    };
  }, [name]);

  return <div className="part-preview" ref={ref} />;
}
