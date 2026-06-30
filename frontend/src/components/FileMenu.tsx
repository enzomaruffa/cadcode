import { useEffect, useRef, useState } from "react";
import { HTTP_URL } from "../config";
import { useStore } from "../lib/store";

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

export function FileMenu() {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState("");
  const ref = useRef<HTMLDivElement>(null);

  const docs = useStore((s) => s.docs);
  const activeDocId = useStore((s) => s.activeDocId);
  const source = useStore((s) => s.source);
  const newDoc = useStore((s) => s.newDoc);
  const openDoc = useStore((s) => s.openDoc);

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
          <div className="menu-sep" />
          <div className="menu-label">Export</div>
          {EXPORTS.map((e) => (
            <button key={e.fmt} className="menu-item" disabled={!!busy} onClick={() => exportAs(e.fmt)}>
              {busy === e.fmt ? `exporting ${e.fmt}…` : e.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
