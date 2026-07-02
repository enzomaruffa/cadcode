import { useEffect, useRef, useState } from "react";
import { EditorPane } from "./components/EditorPane";
import { TabBar } from "./components/TabBar";
import { Viewport } from "./components/Viewport";
import { StatusBar } from "./components/StatusBar";
import { SelectionPanel } from "./components/SelectionPanel";
import { PrintabilityToggle } from "./components/PrintabilityToggle";
import { AgentColumn } from "./components/AgentColumn";
import { ParamsPanel } from "./components/ParamsPanel";
import { LibraryModal } from "./components/LibraryModal";
import { EditorColorsModal } from "./components/EditorColorsModal";
import { DesignTokensModal } from "./components/DesignTokensModal";
import { PrintModal } from "./components/PrintModal";
import { HelpModal } from "./components/HelpModal";
import { FileMenu } from "./components/FileMenu";
import { ProjectMenu } from "./components/ProjectMenu";
import { HistoryControls } from "./components/HistoryControls";
import { useStore } from "./lib/store";

type PaneKey = "agent" | "code" | "viewport";

const MOBILE_BP = 820;
const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));

function useIsMobile(): boolean {
  const [mobile, setMobile] = useState(() => window.matchMedia(`(max-width:${MOBILE_BP}px)`).matches);
  useEffect(() => {
    const mq = window.matchMedia(`(max-width:${MOBILE_BP}px)`);
    const onChange = () => setMobile(mq.matches);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);
  return mobile;
}

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

function PaneTitle({ label, onCollapse }: { label: string; onCollapse: () => void }) {
  return (
    <div className="pane-title">
      <span>{label}</span>
      <button className="pane-collapse" onClick={onCollapse} title={`Collapse ${label}`}>
        ‹
      </button>
    </div>
  );
}

// Pane bodies — shared by both layouts so the renderer/editor stay mounted.
const AgentBody = () => <AgentColumn />;
const CodeBody = () => (
  <>
    <TabBar />
    <EditorPane />
    <ParamsPanel />
  </>
);
const ViewportBody = () => (
  <div className="viewport-wrap">
    <Viewport />
    <PrintabilityToggle />
    <SelectionPanel />
  </div>
);

export default function App() {
  const connect = useStore((s) => s.connect);
  const isMobile = useIsMobile();
  const [collapsed, setCollapsed] = useState<Record<PaneKey, boolean>>({ agent: false, code: false, viewport: false });
  const [agentW, setAgentW] = useState(320);
  const [codeW, setCodeW] = useState(460);
  const [tab, setTab] = useState<PaneKey>("viewport");
  const [showLibrary, setShowLibrary] = useState(false);
  const [showColors, setShowColors] = useState(false);
  const [showTokens, setShowTokens] = useState(false);
  const [showPrint, setShowPrint] = useState(false);
  const [showHelp, setShowHelp] = useState(false);
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

  const showAgentResizer = !collapsed.agent && !collapsed.code;
  const showCodeResizer = !collapsed.code && !collapsed.viewport;

  const topbar = (
    <header className="topbar">
      <FileMenu onEditorColors={() => setShowColors(true)} />
      <span className="brand">cadcode</span>
      <ProjectMenu />
      <button className="lib-btn" onClick={() => setShowLibrary(true)} title="Browse the parts library">
        ⊞ library
      </button>
      <button className="lib-btn" onClick={() => setShowTokens(true)} title="View & edit shared design tokens">
        ⚙ tokens
      </button>
      <button className="lib-btn" onClick={() => setShowPrint(true)} title="Arrange parts on a plate for printing">
        ⎙ print
      </button>
      <button className="lib-btn" onClick={() => setShowHelp(true)} title="build123d quick reference">
        ? docs
      </button>
      <HistoryControls />
    </header>
  );

  const modals = (
    <>
      {showLibrary && <LibraryModal onClose={() => setShowLibrary(false)} />}
      {showColors && <EditorColorsModal onClose={() => setShowColors(false)} />}
      {showTokens && <DesignTokensModal onClose={() => setShowTokens(false)} />}
      {showPrint && <PrintModal onClose={() => setShowPrint(false)} />}
      {showHelp && <HelpModal onClose={() => setShowHelp(false)} />}
    </>
  );

  // --- mobile: one pane at a time + a bottom tab bar. All panes stay mounted
  // (visibility toggled with CSS) so the 3D camera and editor state survive. ---
  if (isMobile) {
    const paneClass = (k: PaneKey) =>
      `pane mpane pane-${k === "code" ? "editor" : k}` + (tab === k ? "" : " mpane-hidden");
    return (
      <div className="app app-mobile">
        {topbar}
        <div className="panes panes-mobile">
          <section className={paneClass("agent")}>
            <AgentBody />
          </section>
          <section className={paneClass("code")}>
            <CodeBody />
          </section>
          <section className={paneClass("viewport")}>
            <ViewportBody />
          </section>
        </div>
        <nav className="mobile-tabs">
          <button className={tab === "agent" ? "on" : ""} onClick={() => setTab("agent")}>
            agent
          </button>
          <button className={tab === "code" ? "on" : ""} onClick={() => setTab("code")}>
            code
          </button>
          <button className={tab === "viewport" ? "on" : ""} onClick={() => setTab("viewport")}>
            3d
          </button>
        </nav>
        <StatusBar />
        {modals}
      </div>
    );
  }

  // --- desktop: resizable / collapsible three-pane layout ---
  return (
    <div className="app">
      {topbar}
      <div className="panes" ref={wrap}>
        {collapsed.agent ? (
          <CollapsedStrip title="agent" onExpand={() => toggle("agent")} />
        ) : (
          <section className="pane pane-agent" style={{ flex: `0 0 ${agentW}px` }}>
            <PaneTitle label="agent" onCollapse={() => toggle("agent")} />
            <AgentBody />
          </section>
        )}
        {showAgentResizer && <Resizer onDrag={(dx) => setAgentW((w) => clamp(w + dx, 240, 640))} />}

        {collapsed.code ? (
          <CollapsedStrip title="code" onExpand={() => toggle("code")} />
        ) : (
          <section className="pane pane-editor" style={{ flex: `0 0 ${codeW}px` }}>
            <PaneTitle label="code" onCollapse={() => toggle("code")} />
            <CodeBody />
          </section>
        )}
        {showCodeResizer && <Resizer onDrag={(dx) => setCodeW((w) => clamp(w + dx, 320, 1000))} />}

        {collapsed.viewport ? (
          <CollapsedStrip title="3d" onExpand={() => toggle("viewport")} />
        ) : (
          <section className="pane pane-viewport">
            <PaneTitle label="3d" onCollapse={() => toggle("viewport")} />
            <ViewportBody />
          </section>
        )}
      </div>

      <StatusBar />
      {modals}
    </div>
  );
}
