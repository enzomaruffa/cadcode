import { useState } from "react";
import { useStore } from "../lib/store";
import { AgentPanel } from "./AgentPanel";
import { FilesPanel } from "./FilesPanel";
import { ProjectAgentPanel } from "./ProjectAgentPanel";

// The left column is the current project + the agent. "files" shows the active
// project's files; "agent" is the whole-project multi-file agent when a project
// is active, or the single-buffer agent for the scratch buffer otherwise.
// (Switching/listing projects lives in the top-bar project menu.)
export function AgentColumn() {
  const [tab, setTab] = useState<"files" | "agent">("agent");
  const activeProject = useStore((s) => s.activeProject);
  return (
    <div className="agent-column">
      <div className="agent-column-tabs">
        <button className={tab === "files" ? "on" : ""} onClick={() => setTab("files")}>
          files
        </button>
        <button className={tab === "agent" ? "on" : ""} onClick={() => setTab("agent")}>
          agent{activeProject ? " ✳" : ""}
        </button>
      </div>
      {tab === "files" ? <FilesPanel /> : activeProject ? <ProjectAgentPanel /> : <AgentPanel />}
    </div>
  );
}
