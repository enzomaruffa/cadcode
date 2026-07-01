import { DiffEditor } from "@monaco-editor/react";
import { useState } from "react";
import { HTTP_URL } from "../config";
import type { Spec, TessShapes } from "../lib/protocol";
import { useStore } from "../lib/store";

interface FileEdit {
  path: string;
  new_source: string;
}
interface ProjectPatch {
  ok: boolean;
  edits?: FileEdit[];
  rationale?: string;
  targets?: string[];
  error?: string;
}

// The whole-project multi-file agent (PROJECTS_PLAN.md). It edits many files at
// once — a part and the scenes/parts that use it — and shows the result as a
// per-file diff you accept as one atomic patch. Accepting writes every file and
// re-runs the run target so the viewport updates.
export function ProjectAgentPanel() {
  const activeProject = useStore((s) => s.activeProject);
  const runTarget = useStore((s) => s.runTarget);
  const renderShapes = useStore((s) => s.renderShapes);

  const [prompt, setPrompt] = useState("");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [patch, setPatch] = useState<ProjectPatch | null>(null);
  const [originals, setOriginals] = useState<Record<string, string>>({});
  const [lastRationale, setLastRationale] = useState<string | null>(null);

  const target = runTarget ?? { kind: "scene", name: "" };

  const fetchFiles = async (): Promise<Record<string, string>> => {
    if (!activeProject) return {};
    try {
      const r = await fetch(`${HTTP_URL}/projects/${activeProject}/files`);
      const d: { files?: Record<string, string> } = await r.json();
      return d.files ?? {};
    } catch {
      return {};
    }
  };

  const runTargetNow = async () => {
    if (!activeProject) return;
    setStatus("running…");
    try {
      // A part has no show() of its own — preview it by wrapping in show(part()).
      const body: Record<string, unknown> = { kind: target.kind, name: target.name };
      if (target.kind === "part" && target.name) {
        body.source = `from parts.${target.name} import ${target.name}\nshow(${target.name}(), name=${JSON.stringify(target.name)})`;
      }
      const r = await fetch(`${HTTP_URL}/projects/${activeProject}/run`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const d: { ok?: boolean; shapes?: TessShapes; specs?: Spec[]; error?: string } = await r.json();
      if (d.ok && d.shapes) {
        renderShapes(d.shapes, d.specs ?? []);
        setStatus(null);
      } else {
        setStatus(d.error ?? "run failed");
      }
    } catch (e) {
      setStatus(String(e));
    }
  };

  const ask = async () => {
    const message = prompt.trim();
    if (!message || !activeProject || busy) return;
    setBusy(true);
    setStatus("thinking…");
    setPatch(null);
    const files = await fetchFiles();
    setOriginals(files);
    try {
      const r = await fetch(`${HTTP_URL}/projects/${activeProject}/agent`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message, run_kind: target.kind, run_name: target.name }),
      });
      const d: ProjectPatch = await r.json();
      if (d.ok && d.edits?.length) {
        setPatch(d);
        setStatus(null);
        setPrompt("");
      } else {
        setStatus(d.error ?? "the agent returned no edits");
      }
    } catch (e) {
      setStatus(String(e));
    } finally {
      setBusy(false);
    }
  };

  const accept = async () => {
    if (!patch?.edits || !activeProject) return;
    setStatus("applying…");
    try {
      await fetch(`${HTTP_URL}/projects/${activeProject}/apply`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ edits: patch.edits }),
      });
      setLastRationale(patch.rationale ?? null);
      setPatch(null);
      await runTargetNow();
    } catch (e) {
      setStatus(String(e));
    }
  };

  const reject = () => {
    setPatch(null);
    setStatus(null);
  };

  if (!activeProject) {
    return (
      <div className="proj-agent">
        <div className="proj-agent-empty">
          Open a project file from <em>files</em> to work with the project agent. It edits the whole project — many
          parts and scenes at once.
        </div>
      </div>
    );
  }

  return (
    <div className="proj-agent">
      <div className="proj-agent-head">
        <div className="proj-agent-target">
          <span className="proj-agent-proj">{activeProject}</span>
          <span className="proj-agent-run">
            runs {target.kind}
            {target.name ? `/${target.name}` : ""}
          </span>
        </div>
        <button className="proj-agent-runbtn" onClick={runTargetNow} disabled={busy} title="Run the target and render">
          ▶ run
        </button>
      </div>

      {lastRationale && !patch && <div className="proj-agent-note">✓ {lastRationale}</div>}
      {status && <div className="proj-agent-status">{status}</div>}

      {patch?.edits && (
        <div className="proj-agent-patch">
          <div className="proj-agent-rationale">{patch.rationale}</div>
          {patch.edits.map((e) => (
            <div className="proj-agent-file" key={e.path}>
              <div className="proj-agent-file-name">
                {e.path}
                {!(e.path in originals) && <span className="proj-agent-new">new</span>}
              </div>
              <div className="proj-agent-diff">
                <DiffEditor
                  original={originals[e.path] ?? ""}
                  modified={e.new_source}
                  language="python"
                  theme="cadcode"
                  options={{
                    renderSideBySide: false,
                    readOnly: true,
                    minimap: { enabled: false },
                    scrollBeyondLastLine: false,
                    lineNumbers: "off",
                    fontSize: 12,
                    folding: false,
                    renderOverviewRuler: false,
                    scrollbar: { vertical: "auto", horizontal: "auto" },
                  }}
                />
              </div>
            </div>
          ))}
          <div className="proj-agent-actions">
            <button className="proj-agent-accept" onClick={accept}>
              accept all
            </button>
            <button className="proj-agent-reject" onClick={reject}>
              reject
            </button>
          </div>
        </div>
      )}

      <div className="proj-agent-input">
        <textarea
          value={prompt}
          onChange={(ev) => setPrompt(ev.target.value)}
          onKeyDown={(ev) => {
            if (ev.key === "Enter" && (ev.metaKey || ev.ctrlKey)) {
              ev.preventDefault();
              void ask();
            }
          }}
          placeholder="Change the whole project — e.g. “make the bracket 2mm thicker and update the enclosure that uses it”. ⌘↵ to send."
          disabled={busy}
        />
        <button onClick={() => void ask()} disabled={busy || !prompt.trim()}>
          {busy ? "…" : "ask"}
        </button>
      </div>
    </div>
  );
}
