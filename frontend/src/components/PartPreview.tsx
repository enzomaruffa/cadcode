import { useEffect, useState } from "react";
import { CadCanvas, type TessShapes } from "@cadcode/viewer";
import { HTTP_URL } from "../config";

interface Props {
  name: string;
  className?: string;
  // When set, preview a PROJECT file (part/scene) via the project runner instead
  // of a global library part. Parts have no show() of their own, so we wrap them.
  project?: string;
  kind?: "part" | "scene";
}

// A standalone, orbitable mini renderer. Non-interactive (no picking/section/
// measure) — just a pretty preview. Keyed so each item gets a fresh camera fit.
// Used for global library parts and for project parts/scenes.
export function PartPreview({ name, className = "part-preview", project, kind = "part" }: Props) {
  const key = `${project ?? ""}:${kind}:${name}`;
  const [data, setData] = useState<{ key: string; shapes: TessShapes } | null>(null);

  useEffect(() => {
    let alive = true;
    const done = (d: { shapes?: TessShapes; error?: string }) => {
      if (alive && d.shapes && !d.error) setData({ key, shapes: d.shapes });
    };
    if (project) {
      const body: Record<string, unknown> = { kind, name };
      if (kind === "part")
        body.source = `from parts.${name} import ${name}\nshow(${name}(), name=${JSON.stringify(name)})`;
      fetch(`${HTTP_URL}/projects/${project}/run`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      })
        .then((r) => r.json())
        .then(done)
        .catch(() => void 0);
    } else {
      fetch(`${HTTP_URL}/library/${name}/geometry`)
        .then((r) => r.json())
        .then(done)
        .catch(() => void 0);
    }
    return () => {
      alive = false;
    };
  }, [key, name, project, kind]);

  // Only show geometry that matches the current key (avoids a stale flash).
  const shapes = data && data.key === key ? data.shapes : null;

  return (
    <CadCanvas
      key={key}
      className={className}
      shapes={shapes}
      geometryRev={shapes ? 1 : 0}
      renderProfile="presentation"
      interactive={false}
    />
  );
}
