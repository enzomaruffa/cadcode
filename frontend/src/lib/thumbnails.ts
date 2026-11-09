import { useCallback, useEffect, useSyncExternalStore, type RefObject } from "react";
import { CadViewer, PRESENTATION, type TessShapes } from "@cadcode/viewer";
import { HTTP_URL } from "../config";

// Library thumbnails: one offscreen CadViewer renders every card to a PNG, so the
// grid costs one WebGL context no matter how many cards it shows. Images are
// cached by the backend's content hash; re-opening the library only asks the
// backend whether each hash is still current.

export interface ThumbSpec {
  name: string;
  project?: string;
  kind?: "part" | "scene";
}

export type ThumbState = { status: "loading" } | { status: "ready"; url: string } | { status: "failed"; error: string };

interface Entry {
  spec: ThumbSpec;
  state: ThumbState;
  hash?: string;
  listeners: Set<() => void>;
  mounts: number;
  visible: number;
  queued: boolean;
  inflight: boolean;
}

const THUMB_W = 320;
const THUMB_H = 180;
const MAX_INFLIGHT = 2;
const LOADING: ThumbState = { status: "loading" };

const entries = new Map<string, Entry>();
const images = new Map<string, string>();
let inflight = 0;
let viewer: CadViewer | null = null;

export const thumbKey = (s: ThumbSpec) => `${s.project ?? ""}:${s.kind ?? "part"}:${s.name}`;

function entryFor(spec: ThumbSpec): Entry {
  const key = thumbKey(spec);
  let e = entries.get(key);
  if (!e) {
    e = { spec, state: LOADING, listeners: new Set(), mounts: 0, visible: 0, queued: false, inflight: false };
    entries.set(key, e);
  }
  return e;
}

function setState(e: Entry, state: ThumbState) {
  e.state = state;
  for (const l of e.listeners) l();
}

function getViewer(): CadViewer {
  if (viewer) return viewer;
  const host = document.createElement("div");
  host.setAttribute("aria-hidden", "true");
  Object.assign(host.style, {
    position: "fixed",
    left: "-10000px",
    top: "0",
    width: `${THUMB_W}px`,
    height: `${THUMB_H}px`,
    pointerEvents: "none",
  });
  document.body.appendChild(host);
  viewer = new CadViewer(host, { up: "Z", control: "trackball", ortho: true });
  viewer.setGrid("off");
  viewer.setPicking(false);
  return viewer;
}

function firstLine(err: string): string {
  const lines = err
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean);
  if (lines[0]?.startsWith("Traceback")) return lines[lines.length - 1];
  return lines[0] ?? "error";
}

interface RunResponse {
  shapes?: TessShapes;
  error?: string;
  content_hash?: string;
  unchanged?: boolean;
}

async function fetchGeometry(e: Entry): Promise<RunResponse> {
  const { project, name } = e.spec;
  const kind = e.spec.kind ?? "part";
  if (!project) return (await fetch(`${HTTP_URL}/library/${name}/geometry`)).json();
  const body: Record<string, unknown> = { kind, name };
  if (kind === "part") body.source = `from parts.${name} import ${name}\nshow(${name}(), name=${JSON.stringify(name)})`;
  if (e.hash) body.if_none_match = e.hash;
  const r = await fetch(`${HTTP_URL}/projects/${project}/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return r.json();
}

async function load(e: Entry) {
  try {
    const d = await fetchGeometry(e);
    if (d.unchanged) return;
    const hash = d.content_hash ?? `g:${thumbKey(e.spec)}`;
    e.hash = d.content_hash;
    if (d.error) return setState(e, { status: "failed", error: firstLine(d.error) });
    if (!d.shapes?.parts?.length) return setState(e, { status: "failed", error: "nothing shown" });
    let url = images.get(hash);
    if (!url) {
      url = getViewer().snapshot(d.shapes, PRESENTATION, THUMB_W, THUMB_H);
      images.set(hash, url);
    }
    setState(e, { status: "ready", url });
  } catch (err) {
    setState(e, { status: "failed", error: err instanceof Error ? err.message : String(err) });
  }
}

function next(): Entry | undefined {
  let fallback: Entry | undefined;
  for (const e of entries.values()) {
    if (!e.queued || e.mounts === 0) continue;
    if (e.visible > 0) return e;
    fallback ??= e;
  }
  return fallback;
}

function pump() {
  while (inflight < MAX_INFLIGHT) {
    const e = next();
    if (!e) return;
    e.queued = false;
    e.inflight = true;
    inflight++;
    void load(e).finally(() => {
      e.inflight = false;
      inflight--;
      pump();
    });
  }
}

function mount(e: Entry) {
  e.mounts++;
  if (e.mounts > 1 || e.inflight) return;
  const settledGlobal = !e.spec.project && e.state.status === "ready";
  if (!settledGlobal) e.queued = true;
}

/** Thumbnail for a library card: queued on mount, prioritized while the element
 *  is on screen, served from the image cache when the content hash is unchanged. */
export function useThumbnail(spec: ThumbSpec, ref: RefObject<Element | null>): ThumbState {
  const { name, project, kind } = spec;
  const subscribe = useCallback(
    (cb: () => void) => {
      const e = entryFor({ name, project, kind });
      e.listeners.add(cb);
      return () => e.listeners.delete(cb);
    },
    [name, project, kind],
  );
  const state = useSyncExternalStore(subscribe, () => entryFor({ name, project, kind }).state);

  useEffect(() => {
    const e = entryFor({ name, project, kind });
    mount(e);
    let seen = false;
    const el = ref.current;
    const io = new IntersectionObserver(
      ([hit]) => {
        if (hit.isIntersecting === seen) return;
        seen = hit.isIntersecting;
        e.visible += seen ? 1 : -1;
        if (seen) pump();
      },
      { rootMargin: "200px" },
    );
    if (el) io.observe(el);
    pump();
    return () => {
      io.disconnect();
      if (seen) e.visible--;
      e.mounts--;
    };
  }, [name, project, kind, ref]);

  return state;
}
