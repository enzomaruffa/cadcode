import { useCallback, useEffect, useState } from "react";
import { HTTP_URL } from "../config";
import { useStore } from "../lib/store";

interface ProjectTree {
  name: string;
  constants: boolean;
  parts: string[];
  scenes: string[];
}

// Starter skeletons for new project files. Parts are parametric functions that
// RETURN an object (no show); scenes assemble + show + require.
const PART_SKELETON = (name: string) => `from build123d import Box, Color

# from project import UNIT, WALL  # project-wide constants

def ${name}(SIZE=20.0):
    part = Box(SIZE, SIZE, SIZE)
    require(part.volume > 0, "${name} has volume")   # this part's own spec
    part.color = Color("#9aa7ff")                     # this part's own colour
    return part
`;

const SCENE_SKELETON = (name: string) => `from build123d import Box

# from parts.<part> import <part>   # assemble the project's parts here

part = Box(20, 20, 5)
show(part, name="${name}")
require(part.volume > 0, "has volume")
`;

// The CURRENT project's files (project.py + parts/ + scenes/). Clicking opens a
// file; the + buttons add parts/scenes. Switching/creating projects lives in the
// top-bar project menu — this pane is scoped to whatever project is active.
export function FilesPanel() {
  const activeProject = useStore((s) => s.activeProject);
  const openDoc = useStore((s) => s.openDoc);
  const setRunTarget = useStore((s) => s.setRunTarget);
  const renameProjectDoc = useStore((s) => s.renameProjectDoc);
  const syncProjectDoc = useStore((s) => s.syncProjectDoc);
  const [tree, setTree] = useState<ProjectTree | null>(null);

  // Only sets state in the async callback; the render guards on activeProject, so
  // a stale tree while switching to "no project" never shows.
  const refresh = useCallback(() => {
    if (!activeProject) return;
    fetch(`${HTTP_URL}/projects`)
      .then((r) => r.json())
      .then((d: { projects?: ProjectTree[] }) => setTree(d.projects?.find((p) => p.name === activeProject) ?? null))
      .catch(() => void 0);
  }, [activeProject]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const open = async (kind: string, name: string, label: string) => {
    if (!activeProject) return;
    try {
      const r = await fetch(`${HTTP_URL}/projects/${activeProject}/file?kind=${kind}&name=${encodeURIComponent(name)}`);
      const d: { source?: string } = await r.json();
      if (typeof d.source === "string") openDoc(label, d.source, { project: activeProject, kind, name });
      setRunTarget(activeProject, kind, name);
    } catch {
      /* ignore */
    }
  };

  // Rename = a real refactor: the backend rewrites every import + call site
  // (this project AND others); we then refresh the tree, retitle/repoint the
  // open tab, and re-sync any other open docs whose imports were rewritten.
  const renameFile = async (kind: "part" | "scene", oldName: string) => {
    if (!activeProject) return;
    const raw = window.prompt(`Rename ${kind} "${oldName}" to:`, oldName);
    if (!raw || raw.trim() === oldName) return;
    try {
      const r = await fetch(`${HTTP_URL}/projects/${activeProject}/rename`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind, old: oldName, new: raw.trim() }),
      });
      const d: { ok?: boolean; new?: string; changed?: string[]; error?: string } = await r.json();
      if (!d.ok || !d.new) {
        window.alert(d.error ?? "rename failed");
        return;
      }
      const newName = d.new;
      await refresh();
      const st = useStore.getState();
      // the renamed file's own tab (fetch its rewritten source)
      const fr = await fetch(
        `${HTTP_URL}/projects/${activeProject}/file?kind=${kind}&name=${encodeURIComponent(newName)}`,
      );
      const fd: { source?: string } = await fr.json();
      if (typeof fd.source === "string") renameProjectDoc(activeProject, kind, oldName, newName, fd.source);
      // any other open docs whose imports were rewritten
      for (const doc of st.docs) {
        const o = doc.origin;
        if (!o || !(d.changed ?? []).includes(`${o.project}/${o.kind}s/${o.name}.py`)) continue;
        if (o.project === activeProject && o.kind === kind && o.name === oldName) continue;
        try {
          const rr = await fetch(
            `${HTTP_URL}/projects/${o.project}/file?kind=${o.kind}&name=${encodeURIComponent(o.name)}`,
          );
          const rd: { source?: string } = await rr.json();
          if (typeof rd.source === "string") syncProjectDoc(o.project, o.kind, o.name, rd.source);
        } catch {
          /* ignore */
        }
      }
    } catch {
      /* ignore */
    }
  };

  const createFile = async (kind: "part" | "scene") => {
    if (!activeProject) return;
    const raw = window.prompt(`New ${kind} name:`);
    if (!raw) return;
    const name = raw.trim();
    const source = kind === "part" ? PART_SKELETON(sanitize(name)) : SCENE_SKELETON(name);
    await fetch(`${HTTP_URL}/projects/${activeProject}/file`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind, name, source }),
    }).catch(() => void 0);
    await refresh();
    open(kind, name, name);
  };

  if (!activeProject) {
    return (
      <div className="files">
        <div className="files-empty">
          No project selected. Pick or create one from the <em>project menu</em> in the top bar.
        </div>
      </div>
    );
  }

  return (
    <div className="files">
      <div className="files-head">
        <span>{activeProject}</span>
        <span className="files-project-add">
          <button onClick={() => createFile("part")} title="New part">
            +part
          </button>
          <button onClick={() => createFile("scene")} title="New scene">
            +scene
          </button>
        </span>
      </div>
      <div className="files-tree">
        <button className="files-file" onClick={() => open("project", "", `${activeProject}/project.py`)}>
          project.py
        </button>
        {(tree?.parts ?? []).map((part) => (
          <div className="files-row" key={`pt-${part}`}>
            <button className="files-file" onClick={() => open("part", part, part)}>
              parts/{part}.py
            </button>
            <button
              className="files-rename"
              onClick={() => renameFile("part", part)}
              title="Rename this part — every import and call site is updated (this project and others)"
            >
              ✎
            </button>
          </div>
        ))}
        {(tree?.scenes ?? []).map((scene) => (
          <div className="files-row" key={`sc-${scene}`}>
            <button className="files-file" onClick={() => open("scene", scene, scene)}>
              scenes/{scene}.py
            </button>
            <button className="files-rename" onClick={() => renameFile("scene", scene)} title="Rename this scene">
              ✎
            </button>
          </div>
        ))}
        {tree && tree.parts.length === 0 && tree.scenes.length === 0 && (
          <div className="files-empty">No parts or scenes yet — add one with +part / +scene.</div>
        )}
      </div>
    </div>
  );
}

// Mirror the backend's identifier sanitization for the part's function name.
function sanitize(name: string): string {
  const s = name
    .trim()
    .toLowerCase()
    .replace(/\W+/g, "_")
    .replace(/^_+|_+$/g, "");
  return !s || /^\d/.test(s) ? `x_${s}` : s;
}
