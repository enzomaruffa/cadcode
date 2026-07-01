import { useEffect, useRef, useState } from "react";
import { HTTP_URL } from "../config";
import { useStore } from "../lib/store";
import { EXAMPLES } from "../lib/examples";

const EXPORTS: { fmt: string; label: string }[] = [
  { fmt: "step", label: "STEP (.step)" },
  { fmt: "stl", label: "STL (.stl)" },
  { fmt: "3mf", label: "3MF (.3mf)" },
  { fmt: "glb", label: "glTF (.glb)" },
  { fmt: "brep", label: "BREP (.brep)" },
];

function download(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export function FileMenu({ onEditorColors }: { onEditorColors: () => void }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState("");
  const ref = useRef<HTMLDivElement>(null);

  const docs = useStore((s) => s.docs);
  const activeDocId = useStore((s) => s.activeDocId);
  const source = useStore((s) => s.source);
  const newDoc = useStore((s) => s.newDoc);
  const openDoc = useStore((s) => s.openDoc);
  const activeProject = useStore((s) => s.activeProject);

  const activeName = docs.find((d) => d.id === activeDocId)?.name ?? "model";
  const slug = activeName.replace(/\s+/g, "_").toLowerCase();

  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);

  const openFile = () => {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = ".py,text/x-python,text/plain";
    input.onchange = () => {
      const f = input.files?.[0];
      if (!f) return;
      f.text().then((text) => openDoc(f.name.replace(/\.py$/, ""), text));
    };
    input.click();
    setOpen(false);
  };

  const saveSource = () => {
    download(new Blob([source], { type: "text/x-python" }), `${slug}.py`);
    setOpen(false);
  };

  const saveToLibrary = async () => {
    const name = window.prompt("Save this part as:", slug);
    if (!name) return;
    // With an active project, offer to save into it (project-scoped); else global.
    const toProject =
      activeProject &&
      window.confirm(`Save into project "${activeProject}"?\n\nOK = project part · Cancel = global library`)
        ? activeProject
        : null;
    setBusy("lib");
    try {
      const res = await fetch(`${HTTP_URL}/library/save`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ source, name, project: toProject }),
      });
      const d: { ok?: boolean; name?: string; project?: string; error?: string } = await res.json();
      if (d.ok) {
        const how = d.project ? `from parts.${d.name} import ${d.name}` : `from lib.parts import ${d.name}`;
        alert(
          `Saved "${d.name}"${d.project ? ` into project "${d.project}"` : " to the global library"}.\nUse it with:  ${how}`,
        );
      } else alert(`Save failed: ${d.error ?? "unknown error"}`);
      setOpen(false);
    } catch (e) {
      alert(`Save failed: ${e}`);
    } finally {
      setBusy("");
    }
  };

  const exportAs = async (fmt: string) => {
    setBusy(fmt);
    try {
      const res = await fetch(`${HTTP_URL}/export`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ source, format: fmt, name: slug }),
      });
      if (!res.ok) {
        alert(`Export failed: ${await res.text()}`);
        return;
      }
      const blob = await res.blob();
      download(blob, `${slug}.${fmt}`);
      setOpen(false);
    } catch (e) {
      alert(`Export failed: ${e}`);
    } finally {
      setBusy("");
    }
  };

  return (
    <div className="filemenu" ref={ref}>
      <button className="filemenu-btn" onClick={() => setOpen((o) => !o)}>
        ☰ file
      </button>
      {open && (
        <div className="menu-dropdown">
          <button
            className="menu-item"
            onClick={() => {
              newDoc();
              setOpen(false);
            }}
          >
            New part
          </button>
          <button className="menu-item" onClick={openFile}>
            Open .py…
          </button>
          <button className="menu-item" onClick={saveSource}>
            Save .py
          </button>
          <button className="menu-item" onClick={saveToLibrary} disabled={busy === "lib"}>
            {busy === "lib" ? "saving…" : "Save to library…"}
          </button>
          <div className="menu-sep" />
          <div className="menu-label">Examples</div>
          {EXAMPLES.map((ex) => (
            <button
              key={ex.name}
              className="menu-item"
              onClick={() => {
                openDoc(ex.name, ex.source);
                setOpen(false);
              }}
            >
              {ex.label}
            </button>
          ))}
          <div className="menu-sep" />
          <div className="menu-label">Export</div>
          {EXPORTS.map((e) => (
            <button key={e.fmt} className="menu-item" disabled={!!busy} onClick={() => exportAs(e.fmt)}>
              {busy === e.fmt ? `exporting ${e.fmt}…` : e.label}
            </button>
          ))}
          <div className="menu-sep" />
          <div className="menu-label">Preferences</div>
          <button
            className="menu-item"
            onClick={() => {
              onEditorColors();
              setOpen(false);
            }}
          >
            Editor colors…
          </button>
        </div>
      )}
    </div>
  );
}
