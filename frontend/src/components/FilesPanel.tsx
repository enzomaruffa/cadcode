import { useEffect, useState } from "react";
import { HTTP_URL } from "../config";
import { useStore } from "../lib/store";

interface ProjectTree {
  name: string;
  constants: boolean;
  parts: string[];
  scenes: string[];
}

// A project file tree (project.py + parts/ + scenes/). Clicking a file opens it
// as an editor tab and makes it the project agent's run target. Lives in the
// agent column (toggle at the top).
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

  return (
    <div className="files">
      <div className="files-head">
        <span>projects</span>
        <button className="files-add" onClick={newProject} title="New project">
          + new
        </button>
      </div>
      <div className="files-tree">
        {projects.length === 0 && (
          <div className="files-empty">
            No projects yet. Create one, or use <em>File → Save to library</em>.
          </div>
        )}
        {projects.map((p) => (
          <div className="files-project" key={p.name}>
            <div className="files-project-name">{p.name}</div>
            {p.constants && (
              <button className="files-file" onClick={() => open(p.name, "project", "", `${p.name}/project.py`)}>
                project.py
              </button>
            )}
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
                <button
                  className="files-run"
                  onClick={() => runScene(p.name, scene)}
                  title="Run this scene in the project agent"
                >
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
