import { useEffect, useRef, useState } from "react";
import { HTTP_URL } from "../config";
import { useStore } from "../lib/store";

interface Tree {
  name: string;
  constants: boolean;
  parts: string[];
  scenes: string[];
}

// The current-project switcher — lives in the top bar. Listing / switching /
// creating projects is a workspace concern (not part of the agent column), so
// it belongs here. Picking a project sets it active and opens a sensible file.
export function ProjectMenu() {
  const activeProject = useStore((s) => s.activeProject);
  const setActiveProject = useStore((s) => s.setActiveProject);
  const setRunTarget = useStore((s) => s.setRunTarget);
  const openDoc = useStore((s) => s.openDoc);
  const [open, setOpen] = useState(false);
  const [projects, setProjects] = useState<Tree[]>([]);
  const ref = useRef<HTMLDivElement>(null);

  const refresh = () =>
    fetch(`${HTTP_URL}/projects`)
      .then((r) => r.json())
      .then((d: { projects?: Tree[] }) => setProjects(d.projects ?? []))
      .catch(() => void 0);

  useEffect(() => {
    refresh();
  }, []);

  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);

  const switchTo = async (t: Tree) => {
    setActiveProject(t.name);
    setOpen(false);
    // Open a sensible starting file so the files pane, agent, and viewport have
    // a target: prefer a scene, else a part, else project.py.
    const first = t.scenes[0]
      ? { kind: "scene", name: t.scenes[0] }
      : t.parts[0]
        ? { kind: "part", name: t.parts[0] }
        : { kind: "project", name: "" };
    try {
      const r = await fetch(
        `${HTTP_URL}/projects/${t.name}/file?kind=${first.kind}&name=${encodeURIComponent(first.name)}`,
      );
      const d: { source?: string } = await r.json();
      const label = first.kind === "project" ? `${t.name}/project.py` : first.name;
      if (typeof d.source === "string")
        openDoc(label, d.source, { project: t.name, kind: first.kind, name: first.name });
      setRunTarget(t.name, first.kind, first.name);
    } catch {
      /* ignore */
    }
  };

  const newProject = async () => {
    const name = window.prompt("New project name:");
    if (!name) return;
    const r = await fetch(`${HTTP_URL}/projects`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    })
      .then((res) => res.json())
      .catch(() => null);
    await refresh();
    if (r?.name) setActiveProject(r.name);
    setOpen(false);
  };

  return (
    <div className="projectmenu" ref={ref}>
      <button
        className="lib-btn"
        onClick={() => {
          setOpen((o) => !o);
          refresh();
        }}
        title="Switch / create project"
      >
        ▾ {activeProject ?? "no project"}
      </button>
      {open && (
        <div className="menu-dropdown">
          <div className="menu-label">Projects</div>
          {projects.length === 0 && <div className="menu-empty">none yet</div>}
          {projects.map((p) => (
            <button
              key={p.name}
              className={"menu-item" + (p.name === activeProject ? " on" : "")}
              onClick={() => switchTo(p)}
            >
              {p.name}
            </button>
          ))}
          <div className="menu-sep" />
          <button className="menu-item" onClick={newProject}>
            + New project
          </button>
        </div>
      )}
    </div>
  );
}
