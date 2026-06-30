import { useEffect, useRef, useState } from "react";
import { EditorPane } from "./components/EditorPane";
import { Viewport } from "./components/Viewport";
import { StatusBar } from "./components/StatusBar";
import { SelectionPanel } from "./components/SelectionPanel";
import { PrintabilityToggle } from "./components/PrintabilityToggle";
import { AgentPanel } from "./components/AgentPanel";
import { ParamsPanel } from "./components/ParamsPanel";
import { LibraryModal } from "./components/LibraryModal";
import { HistoryControls } from "./components/HistoryControls";
import { useStore } from "./lib/store";

type PaneKey = "agent" | "code" | "viewport";

const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));

function Resizer({ onDrag }: { onDrag: (dx: number) => void }) {
  const onPointerDown = (e: React.PointerEvent) => {
    e.preventDefault();
    let last = e.clientX;
    const move = (ev: PointerEvent) => {
      onDrag(ev.clientX - last);
      last = ev.clientX;
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
  };
  return <div className="resizer" onPointerDown={onPointerDown} title="Drag to resize" />;
}

function CollapsedStrip({ title, onExpand }: { title: string; onExpand: () => void }) {
  return (
    <button className="pane-strip" onClick={onExpand} title={`Expand ${title}`}>
      <span className="pane-strip-icon">›</span>
      <span className="pane-strip-label">{title}</span>
    </button>
  );
}

export default function App() {
  const connect = useStore((s) => s.connect);
  const [collapsed, setCollapsed] = useState<Record<PaneKey, boolean>>({ agent: false, code: false, viewport: false });
  const [agentW, setAgentW] = useState(320);
  const [codeW, setCodeW] = useState(460);
  const [showLibrary, setShowLibrary] = useState(false);
  const wrap = useRef<HTMLDivElement>(null);

  useEffect(() => {
    connect();
  }, [connect]);

  const toggle = (k: PaneKey) =>
    setCollapsed((c) => {
      const next = { ...c, [k]: !c[k] };
      if (next.agent && next.code && next.viewport) return c; // keep at least one open
      return next;
    });

  const PaneTitle = ({ pane, label, extra }: { pane: PaneKey; label: string; extra?: React.ReactNode }) => (
    <div className="pane-title">
      <span>{label}</span>
      <span className="pane-title-actions">
        {extra}
        <button className="pane-collapse" onClick={() => toggle(pane)} title={`Collapse ${label}`}>
          ‹
        </button>
      </span>
    </div>
  );

  const showAgentResizer = !collapsed.agent && !collapsed.code;
  const showCodeResizer = !collapsed.code && !collapsed.viewport;

  return (
    <div className="app">
      <header className="topbar">
        <span className="brand">cadcode</span>
        <span className="tagline">the canvas is code</span>
        <button className="lib-btn" onClick={() => setShowLibrary(true)} title="Browse the parts library">
          ⊞ library
        </button>
        <HistoryControls />
      </header>

      <div className="panes" ref={wrap}>
        {/* AGENT */}
        {collapsed.agent ? (
          <CollapsedStrip title="agent" onExpand={() => toggle("agent")} />
        ) : (
          <section className="pane pane-agent" style={{ flex: `0 0 ${agentW}px` }}>
            <PaneTitle pane="agent" label="agent" />
            <AgentPanel />
          </section>
        )}
        {showAgentResizer && <Resizer onDrag={(dx) => setAgentW((w) => clamp(w + dx, 240, 640))} />}

        {/* CODE */}
        {collapsed.code ? (
          <CollapsedStrip title="code" onExpand={() => toggle("code")} />
        ) : (
          <section className="pane pane-editor" style={{ flex: `0 0 ${codeW}px` }}>
            <PaneTitle pane="code" label="code" />
            <EditorPane />
            <ParamsPanel />
          </section>
        )}
        {showCodeResizer && <Resizer onDrag={(dx) => setCodeW((w) => clamp(w + dx, 320, 1000))} />}

        {/* 3D */}
        {collapsed.viewport ? (
          <CollapsedStrip title="3d" onExpand={() => toggle("viewport")} />
        ) : (
          <section className="pane pane-viewport">
            <PaneTitle pane="viewport" label="3d" />
            <div className="viewport-wrap">
              <Viewport />
              <PrintabilityToggle />
              <SelectionPanel />
            </div>
          </section>
        )}
      </div>

      <StatusBar />
      {showLibrary && <LibraryModal onClose={() => setShowLibrary(false)} />}
    </div>
  );
}
