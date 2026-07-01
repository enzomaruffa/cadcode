import { useState } from "react";
import { AgentPanel } from "./AgentPanel";
import { FilesPanel } from "./FilesPanel";

// The left column toggles between the agent chat and the project file tree — one
// column, two views, to keep the screen count down.
export function AgentColumn() {
  const [tab, setTab] = useState<"agent" | "files">("agent");
  return (
    <div className="agent-column">
      <div className="agent-column-tabs">
        <button className={tab === "agent" ? "on" : ""} onClick={() => setTab("agent")}>
          agent
        </button>
        <button className={tab === "files" ? "on" : ""} onClick={() => setTab("files")}>
          files
        </button>
      </div>
      {tab === "agent" ? <AgentPanel /> : <FilesPanel />}
    </div>
  );
}
