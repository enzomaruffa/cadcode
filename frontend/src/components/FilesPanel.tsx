import { useEffect, useState } from "react";
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
const PART_SKELETON = (name: string) => `from build123d import Box

# from project import UNIT, WALL  # project-wide constants

def ${name}(SIZE=20.0):
    return Box(SIZE, SIZE, SIZE)
`;

const SCENE_SKELETON = (name: string) => `from build123d import Box

# from parts.<part> import <part>   # assemble the project's parts here

part = Box(20, 20, 5)
show(part, name="${name}")
require(part.volume > 0, "has volume")
`;

// A project file tree (project.py + parts/ + scenes/). Clicking a file opens it
// as an editor tab and makes it the project agent's run target; the + buttons
// add new parts/scenes. Lives in the agent column (toggle at the top).
export function FilesPanel({ onOpenProject }: { onOpenProject?: () => void }) {
  const [projects, setProjects] = useState<ProjectTree[]>([]);
  const openDoc = useStore((s) => s.openDoc);
  const setRunTarget = useStore((s) => s.setRunTarget);

  const refresh = () =>
    fetch(`${HTTP_URL}/projects`)
      .then((r) => r.json())
      .then((d: { projects?: ProjectTree[] }) => setProjects(d.projects ?? []))
      .catch(() => void 0);

  useEffect(() => {
    refresh();
  }, []);

  const open = async (project: string, kind: string, name: string, label: string) => {
    try {
      const r = await fetch(`${HTTP_URL}/projects/${project}/file?kind=${kind}&name=${encodeURIComponent(name)}`);
      const d: { source?: string } = await r.json();
      // Tag the tab with its project origin so live-runs go through the project
      // runner (project on sys.path), not the single-buffer kernel.
      if (typeof d.source === "string") openDoc(label, d.source, { project, kind, name });
      // Make this the project agent's run/render target (scenes render; the
      // agent edits the whole project around whatever file you're on).
      setRunTarget(project, kind, name);
    } catch {
      /* ignore */
    }
  };

  const runScene = (project: string, scene: string) => {
    setRunTarget(project, "scene", scene);
    onOpenProject?.();
  };

  const newProject = async () => {
    const name = window.prompt("New project name:");
    if (!name) return;
    await fetch(`${HTTP_URL}/projects`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    }).catch(() => void 0);
    refresh();
  };

  const createFile = async (project: string, kind: "part" | "scene") => {
    const raw = window.prompt(`New ${kind} name:`);
    if (!raw) return;
    const name = raw.trim();
    const source = kind === "part" ? PART_SKELETON(sanitize(name)) : SCENE_SKELETON(name);
    await fetch(`${HTTP_URL}/projects/${project}/file`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind, name, source }),
    }).catch(() => void 0);
    await refresh();
    open(project, kind, name, name);
  };

  return (
    <div className="files">
      <div className="files-head">
        <span>projects</span>
        <button className="files-add" onClick={newProject} title="New project">
          + project
        </button>
      </div>
      <div className="files-tree">
        {projects.length === 0 && <div className="files-empty">No projects yet — create one with + project.</div>}
        {projects.map((p) => (
          <div className="files-project" key={p.name}>
            <div className="files-project-head">
              <span className="files-project-name">{p.name}</span>
              <span className="files-project-add">
                <button onClick={() => createFile(p.name, "part")} title="New part">
                  +part
                </button>
                <button onClick={() => createFile(p.name, "scene")} title="New scene">
                  +scene
                </button>
              </span>
            </div>
            <button className="files-file" onClick={() => open(p.name, "project", "", `${p.name}/project.py`)}>
              project.py
            </button>
            {p.parts.map((part) => (
              <button className="files-file" key={`pt-${part}`} onClick={() => open(p.name, "part", part, part)}>
                parts/{part}.py
              </button>
            ))}
            {p.scenes.map((scene) => (
              <div className="files-file-row" key={`sc-${scene}`}>
                <button className="files-file" onClick={() => open(p.name, "scene", scene, scene)}>
                  scenes/{scene}.py
                </button>
                <button className="files-run" onClick={() => runScene(p.name, scene)} title="Run this scene">
                  ▶
                </button>
              </div>
            ))}
          </div>
        ))}
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
