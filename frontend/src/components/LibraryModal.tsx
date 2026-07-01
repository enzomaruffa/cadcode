import { useEffect, useMemo, useState } from "react";
import { HTTP_URL } from "../config";
import { useStore } from "../lib/store";
import { PartPreview } from "./PartPreview";

interface CatalogPart {
  name: string;
  signature: string;
  doc: string;
  import: string;
}
interface ProjectTree {
  name: string;
  constants: boolean;
  parts: string[];
  scenes: string[];
}

// A library item is either a GLOBAL catalog part or a file inside a PROJECT.
type Item =
  | { scope: "global"; name: string; part: CatalogPart }
  | { scope: "project"; project: string; kind: "part" | "scene"; name: string };

const itemKey = (it: Item) => (it.scope === "global" ? `g:${it.name}` : `p:${it.project}:${it.kind}:${it.name}`);
const itemName = (it: Item) => it.name;

// The library is a PROJECTS viewer AND a PARTS viewer: browse every project's
// parts + scenes (rendered live) alongside the global reusable-parts catalog.
export function LibraryModal({ onClose }: { onClose: () => void }) {
  const [parts, setParts] = useState<CatalogPart[]>([]);
  const [projects, setProjects] = useState<ProjectTree[]>([]);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<Item | null>(null);
  const sendChat = useStore((s) => s.sendChat);
  const openDoc = useStore((s) => s.openDoc);
  const setRunTarget = useStore((s) => s.setRunTarget);

  useEffect(() => {
    Promise.all([
      fetch(`${HTTP_URL}/library`)
        .then((r) => r.json())
        .then((d: { parts?: CatalogPart[] }) => setParts(d.parts ?? []))
        .catch(() => void 0),
      fetch(`${HTTP_URL}/projects`)
        .then((r) => r.json())
        .then((d: { projects?: ProjectTree[] }) => setProjects(d.projects ?? []))
        .catch(() => void 0),
    ]).finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      if (selected) setSelected(null);
      else onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, selected]);

  const q = query.trim().toLowerCase();
  const globalItems: Item[] = useMemo(
    () =>
      parts
        .filter((p) => !q || (p.name + " " + p.doc).toLowerCase().includes(q))
        .map((p) => ({ scope: "global", name: p.name, part: p })),
    [parts, q],
  );
  const projectMatches = (name: string, project: string) => !q || (name + " " + project).toLowerCase().includes(q);

  const addGlobal = (p: CatalogPart) => {
    sendChat(`Add a ${p.name} from the parts library to the model.`);
    onClose();
  };

  const openProjectFile = async (project: string, kind: "part" | "scene", name: string) => {
    try {
      const r = await fetch(`${HTTP_URL}/projects/${project}/file?kind=${kind}&name=${encodeURIComponent(name)}`);
      const d: { source?: string } = await r.json();
      if (typeof d.source === "string") openDoc(name, d.source, { project, kind, name });
      setRunTarget(project, kind, name);
    } catch {
      /* ignore */
    }
    onClose();
  };

  const detailPreview = (it: Item) =>
    it.scope === "global" ? (
      <PartPreview name={it.name} />
    ) : (
      <PartPreview name={it.name} project={it.project} kind={it.kind} />
    );

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          {selected ? (
            <button className="modal-back" onClick={() => setSelected(null)}>
              ‹ library
            </button>
          ) : (
            <span className="modal-title">library</span>
          )}
          {!selected && (
            <input
              className="lib-search"
              placeholder="search projects & parts…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              autoFocus
            />
          )}
          <button className="modal-close" onClick={onClose}>
            ×
          </button>
        </div>

        <div className="modal-body">
          {selected ? (
            <div className="lib-detail">
              {detailPreview(selected)}
              <div className="lib-detail-meta">
                <div className="lib-name">{itemName(selected)}</div>
                {selected.scope === "global" ? (
                  <>
                    <div className="lib-doc">{selected.part.doc}</div>
                    <code className="lib-sig">{selected.part.signature}</code>
                    <code className="lib-sig">{selected.part.import}</code>
                    <div className="lib-detail-actions">
                      <button className="lib-add" onClick={() => addGlobal(selected.part)}>
                        add to model
                      </button>
                      <span className="lib-hint">drag in the preview to rotate</span>
                    </div>
                  </>
                ) : (
                  <>
                    <div className="lib-doc">
                      {selected.kind} in project <code>{selected.project}</code>
                    </div>
                    <code className="lib-sig">
                      {selected.kind === "part"
                        ? `from parts.${selected.name} import ${selected.name}`
                        : `scenes/${selected.name}.py`}
                    </code>
                    <div className="lib-detail-actions">
                      <button
                        className="lib-add"
                        onClick={() => openProjectFile(selected.project, selected.kind, selected.name)}
                      >
                        open in editor
                      </button>
                      <span className="lib-hint">drag in the preview to rotate</span>
                    </div>
                  </>
                )}
              </div>
            </div>
          ) : (
            <>
              {loading && <div className="lib-empty">loading…</div>}

              {!loading &&
                projects.map((p) => {
                  const partItems = p.parts.filter((n) => projectMatches(n, p.name));
                  const sceneItems = p.scenes.filter((n) => projectMatches(n, p.name));
                  if (partItems.length === 0 && sceneItems.length === 0) return null;
                  return (
                    <div className="lib-section" key={`proj-${p.name}`}>
                      <div className="lib-section-head">
                        <span className="lib-section-title">{p.name}</span>
                        <span className="lib-section-tag">project</span>
                      </div>
                      <div className="lib-grid">
                        {partItems.map((n) => {
                          const it: Item = { scope: "project", project: p.name, kind: "part", name: n };
                          return (
                            <button key={itemKey(it)} className="lib-card" onClick={() => setSelected(it)}>
                              <PartPreview name={n} project={p.name} kind="part" className="lib-thumb" />
                              <div className="lib-meta">
                                <div className="lib-name">{n}</div>
                                <div className="lib-doc">part</div>
                              </div>
                            </button>
                          );
                        })}
                        {sceneItems.map((n) => {
                          const it: Item = { scope: "project", project: p.name, kind: "scene", name: n };
                          return (
                            <button key={itemKey(it)} className="lib-card" onClick={() => setSelected(it)}>
                              <PartPreview name={n} project={p.name} kind="scene" className="lib-thumb" />
                              <div className="lib-meta">
                                <div className="lib-name">{n}</div>
                                <div className="lib-doc">scene</div>
                              </div>
                            </button>
                          );
                        })}
                      </div>
                    </div>
                  );
                })}

              {!loading && globalItems.length > 0 && (
                <div className="lib-section">
                  <div className="lib-section-head">
                    <span className="lib-section-title">global parts</span>
                    <span className="lib-section-tag">lib.parts</span>
                  </div>
                  <div className="lib-grid">
                    {globalItems.map((it) =>
                      it.scope === "global" ? (
                        <button key={itemKey(it)} className="lib-card" onClick={() => setSelected(it)}>
                          <PartPreview name={it.name} className="lib-thumb" />
                          <div className="lib-meta">
                            <div className="lib-name">{it.name}</div>
                            <div className="lib-doc">{(it.part.doc || "").split("\n")[0]}</div>
                          </div>
                        </button>
                      ) : null,
                    )}
                  </div>
                </div>
              )}

              {!loading && globalItems.length === 0 && projects.every((p) => !p.parts.length && !p.scenes.length) && (
                <div className="lib-empty">Nothing here yet — create a project or save a part.</div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
