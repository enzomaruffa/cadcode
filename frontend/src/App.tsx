import { useEffect } from "react";
import { EditorPane } from "./components/EditorPane";
import { Viewport } from "./components/Viewport";
import { StatusBar } from "./components/StatusBar";
import { SelectionPanel } from "./components/SelectionPanel";
import { useStore } from "./lib/store";

export default function App() {
  const connect = useStore((s) => s.connect);

  useEffect(() => {
    connect();
  }, [connect]);

  return (
    <div className="app">
      <header className="topbar">
        <span className="brand">cadcode</span>
        <span className="tagline">the canvas is code</span>
      </header>
      <div className="panes">
        <section className="pane pane-editor">
          <div className="pane-title">source</div>
          <EditorPane />
        </section>
        <section className="pane pane-viewport">
          <div className="pane-title">geometry</div>
          <div className="viewport-wrap">
            <Viewport />
            <SelectionPanel />
          </div>
        </section>
      </div>
      <StatusBar />
    </div>
  );
}
