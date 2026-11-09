import { useEffect, useRef, useState } from "react";
import { CadCanvas, type CadCanvasHandle, type TessShapes } from "@cadcode/viewer";
import { HTTP_URL } from "../config";
import { useThumbnail } from "../lib/thumbnails";

interface Props {
  name: string;
  className?: string;
  // When set, preview a PROJECT file (part/scene) via the project runner instead
  // of a global library part. Parts have no show() of their own, so we wrap them.
  project?: string;
  kind?: "part" | "scene";
  // Card mode: no grid or gizmo, framed like the static thumbnail it replaces.
  bare?: boolean;
}

// A standalone, orbitable mini renderer. Non-interactive (no picking/section/
// measure) — just a pretty preview. Keyed so each item gets a fresh camera fit.
// Used for global library parts and for project parts/scenes.
export function PartPreview({ name, className = "part-preview", project, kind = "part", bare = false }: Props) {
  const canvas = useRef<CadCanvasHandle>(null);
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

  useEffect(() => {
    const c = canvas.current;
    if (!bare || !shapes || !c) return;
    c.setGrid("off");
    c.setGizmo(false);
    c.frameTight();
  }, [bare, shapes]);

  return (
    <CadCanvas
      key={key}
      ref={canvas}
      className={className}
      shapes={shapes}
      geometryRev={shapes ? 1 : 0}
      renderProfile="presentation"
      interactive={false}
    />
  );
}

// A library card's static image from the shared thumbnail renderer. With `live`
// it layers an orbitable PartPreview over the image; the library keeps at most
// one card live so the grid never holds more than one extra WebGL context.
export function PartThumb({
  name,
  project,
  kind = "part",
  live = false,
}: Omit<Props, "className"> & { live?: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  const thumb = useThumbnail({ name, project, kind }, ref);
  return (
    <div
      ref={ref}
      className={`lib-thumb lib-thumb-${thumb.status}`}
      title={thumb.status === "failed" ? thumb.error : undefined}
    >
      {thumb.status === "ready" && <img src={thumb.url} alt="" draggable={false} />}
      {thumb.status === "failed" && <span className="lib-thumb-note">doesn't build</span>}
      {live && thumb.status === "ready" && (
        <PartPreview name={name} project={project} kind={kind} className="lib-thumb-live" bare />
      )}
    </div>
  );
}
