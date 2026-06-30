import { useEffect, useState } from "react";
import { EditorPane } from "./components/EditorPane";
import { Viewport } from "./components/Viewport";
import { StatusBar } from "./components/StatusBar";
import { SelectionPanel } from "./components/SelectionPanel";
import { PrintabilityToggle } from "./components/PrintabilityToggle";
import { AgentPanel } from "./components/AgentPanel";
import { ParamsPanel } from "./components/ParamsPanel";
import { PartsPalette } from "./components/PartsPalette";
import { HistoryControls } from "./components/HistoryControls";
import { useStore } from "./lib/store";

type PaneKey = "editor" | "viewport" | "agent";

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
  const [collapsed, setCollapsed] = useState<Record<PaneKey, boolean>>({
    editor: false,
    viewport: false,
    agent: false,
  });

  useEffect(() => {
    connect();
  }, [connect]);

  const toggle = (k: PaneKey) =>
    setCollapsed((c) => {
      const next = { ...c, [k]: !c[k] };
      if (next.editor && next.viewport && next.agent) return c; // keep at least one open
      return next;
    });

  const PaneTitle = ({ pane, label }: { pane: PaneKey; label: string }) => (
    <div className="pane-title">
      <span>{label}</span>
      <button className="pane-collapse" onClick={() => toggle(pane)} title={`Collapse ${label}`}>
        ‹
      </button>
    </div>
  );

  return (
    <div className="app">
      <header className="topbar">
        <span className="brand">cadcode</span>
        <span className="tagline">the canvas is code</span>
        <HistoryControls />
      </header>
      <div className="panes">
        {collapsed.editor ? (
          <CollapsedStrip title="source" onExpand={() => toggle("editor")} />
        ) : (
          <section className="pane pane-editor">
            <PaneTitle pane="editor" label="source" />
            <EditorPane />
            <ParamsPanel />
          </section>
        )}

        {collapsed.viewport ? (
          <CollapsedStrip title="geometry" onExpand={() => toggle("viewport")} />
        ) : (
          <section className="pane pane-viewport">
            <PaneTitle pane="viewport" label="geometry" />
            <div className="viewport-wrap">
              <Viewport />
              <PrintabilityToggle />
              <SelectionPanel />
            </div>
          </section>
        )}

        {collapsed.agent ? (
          <CollapsedStrip title="agent" onExpand={() => toggle("agent")} />
        ) : (
          <section className="pane pane-agent">
            <PaneTitle pane="agent" label="agent" />
            <PartsPalette />
            <AgentPanel />
          </section>
        )}
      </div>
      <StatusBar />
    </div>
  );
}
