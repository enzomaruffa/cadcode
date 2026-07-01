import { useEffect, useState } from "react";
import { CadCanvas, type TessShapes } from "@cadcode/viewer";
import { HTTP_URL } from "../config";

// A standalone, orbitable mini renderer for one library part. Non-interactive
// (no picking/section/measure) — just a pretty preview. Keyed by name so each
// part gets a fresh camera fit. Used both for the grid cards and the detail view.
export function PartPreview({ name, className = "part-preview" }: { name: string; className?: string }) {
  const [data, setData] = useState<{ name: string; shapes: TessShapes } | null>(null);

  useEffect(() => {
    let alive = true;
    fetch(`${HTTP_URL}/library/${name}/geometry`)
      .then((r) => r.json())
      .then((d: { shapes?: TessShapes; error?: string }) => {
        if (alive && d.shapes && !d.error) setData({ name, shapes: d.shapes });
      })
      .catch(() => void 0);
    return () => {
      alive = false;
    };
  }, [name]);

  // Only show geometry that matches the current name (avoids a stale flash).
  const shapes = data && data.name === name ? data.shapes : null;

  return (
    <CadCanvas
      key={name}
      className={className}
      shapes={shapes}
      geometryRev={shapes ? 1 : 0}
      renderProfile="presentation"
      interactive={false}
    />
  );
}
