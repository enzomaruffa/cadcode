import { useState } from "react";
import { AgentPanel } from "./AgentPanel";
import { FilesPanel } from "./FilesPanel";
import { ProjectAgentPanel } from "./ProjectAgentPanel";

// The left column toggles between the single-buffer agent, the project file
// tree, and the whole-project multi-file agent — one column, three views, to
// keep the screen count down.
export function AgentColumn() {
  const [tab, setTab] = useState<"agent" | "files" | "project">("agent");
  return (
    <div className="agent-column">
      <div className="agent-column-tabs">
        <button className={tab === "agent" ? "on" : ""} onClick={() => setTab("agent")}>
          agent
        </button>
        <button className={tab === "files" ? "on" : ""} onClick={() => setTab("files")}>
          files
        </button>
        <button className={tab === "project" ? "on" : ""} onClick={() => setTab("project")}>
          project ✳
        </button>
      </div>
      {tab === "agent" && <AgentPanel />}
      {tab === "files" && <FilesPanel onOpenProject={() => setTab("project")} />}
      {tab === "project" && <ProjectAgentPanel />}
    </div>
  );
}
