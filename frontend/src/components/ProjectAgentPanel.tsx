import { useState } from "react";
import { HTTP_URL } from "../config";
import type { Spec, TessShapes } from "../lib/protocol";
import { useStore } from "../lib/store";

interface FileEdit {
  path: string;
  new_source: string;
}
interface AgentResult {
  ok: boolean;
  edits?: FileEdit[];
  rationale?: string;
  targets?: string[];
  note?: string; // one-line durable memory (written to project notes on accept)
  error?: string;
}

// Parse a response as JSON, tolerating a non-JSON body (e.g. a proxy timeout /
// error page) instead of throwing a raw "JSON.parse: unexpected character".
async function readJson<T>(r: Response): Promise<T | null> {
  try {
    return JSON.parse(await r.text()) as T;
  } catch {
    return null;
  }
}

// A project-relative path -> the {kind, name} an open tab is keyed by.
function pathToTarget(path: string): { kind: string; name: string } {
  const p = path.replace(/^\//, "");
  if (p === "project.py") return { kind: "project", name: "" };
  const m = /^(parts|scenes)\/(.+)\.py$/.exec(p);
  if (m) return { kind: m[1] === "parts" ? "part" : "scene", name: m[2] };
  return { kind: "part", name: p.replace(/\.py$/, "") };
}

// The whole-project multi-file agent (PROJECTS_PLAN.md). It edits many files at
// once. The proposed change is shown as an inline diff IN THE CODE EDITOR for
// each changed file (this panel lists them + accepts/rejects the whole patch).
export function ProjectAgentPanel() {
  const activeProject = useStore((s) => s.activeProject);
  const runTarget = useStore((s) => s.runTarget);
  const renderShapes = useStore((s) => s.renderShapes);
  const openDoc = useStore((s) => s.openDoc);
  const projectPatch = useStore((s) => s.projectPatch);
  const setProjectPatch = useStore((s) => s.setProjectPatch);
  const acceptProjectPatch = useStore((s) => s.acceptProjectPatch);
  const rejectProjectPatch = useStore((s) => s.rejectProjectPatch);

  const [prompt, setPrompt] = useState("");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [lastRationale, setLastRationale] = useState<string | null>(null);
  const [originals, setOriginals] = useState<Record<string, string>>({});

  const target = runTarget ?? { kind: "scene", name: "" };
  const patch = projectPatch && projectPatch.project === activeProject ? projectPatch : null;

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
      const body: Record<string, unknown> = { kind: target.kind, name: target.name };
      if (target.kind === "part" && target.name) {
        body.source = `from parts.${target.name} import ${target.name}\nshow(${target.name}(), name=${JSON.stringify(target.name)})`;
      }
      const r = await fetch(`${HTTP_URL}/projects/${activeProject}/run`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const d = await readJson<{ ok?: boolean; shapes?: TessShapes; specs?: Spec[]; error?: string }>(r);
      if (d?.ok && d.shapes) {
        renderShapes(d.shapes, d.specs ?? []);
        setStatus(null);
      } else {
        setStatus(d?.error ?? "run failed (server error or timeout)");
      }
    } catch (e) {
      setStatus(String(e));
    }
  };

  // Open a changed file as a tab (at its CURRENT source, so the editor diffs
  // current → proposed) and focus it.
  const openEdited = (originals: Record<string, string>, path: string) => {
    const t = pathToTarget(path);
    const label = t.kind === "project" ? `${activeProject}/project.py` : t.name;
    openDoc(label, originals[path.replace(/^\//, "")] ?? "", { project: activeProject!, kind: t.kind, name: t.name });
  };

  const ask = async () => {
    const message = prompt.trim();
    if (!message || !activeProject || busy) return;
    setBusy(true);
    setStatus("thinking…");
    rejectProjectPatch();
    const files = await fetchFiles();
    setOriginals(files);
    try {
      // The agent runs as a background job (it can take minutes on a hard request
      // — LLM round-trips + dry-run self-correction), so start it and poll. Each
      // poll is a short request, immune to gateway timeouts.
      const start = await fetch(`${HTTP_URL}/projects/${activeProject}/agent`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message, run_kind: target.kind, run_name: target.name }),
      });
      const started = await readJson<{ ok?: boolean; job_id?: string; error?: string }>(start);
      if (!started?.ok || !started.job_id) {
        setStatus(started?.error ?? "couldn't start the agent — reload and try again");
        return;
      }
      const t0 = Date.now();
      const MAX_MS = 15 * 60_000; // hard stop so an expired session can't poll forever
      let d: AgentResult | null = null;
      while (Date.now() - t0 < MAX_MS) {
        await new Promise((res) => setTimeout(res, 2500));
        const p = await fetch(`${HTTP_URL}/projects/${activeProject}/agent/${started.job_id}`).catch(() => null);
        const poll = p ? await readJson<{ status?: string; result?: AgentResult; log?: string[] }>(p) : null;
        if (!poll) continue; // transient blip / non-JSON — keep polling
        // Live status: the agent's own step log (dry-runs, validation, vision…).
        const mins = Math.floor((Date.now() - t0) / 60000);
        const last = poll.log?.length ? poll.log[poll.log.length - 1] : "thinking…";
        setStatus(mins > 0 ? `${last} (${mins}m)` : last);
        if (poll.status === "done" || poll.status === "gone") {
          d = poll.result ?? null;
          break;
        }
      }
      if (!d) {
        setStatus("The agent didn't return a result — try again.");
        return;
      }
      if (d.ok && d.edits?.length) {
        // Open every changed file (last-opened = first edit, so it's focused) and
        // stage the patch → the code editor shows each file's diff inline.
        for (let i = d.edits.length - 1; i >= 0; i--) openEdited(files, d.edits[i].path);
        setProjectPatch({
          project: activeProject,
          rationale: d.rationale ?? "",
          edits: d.edits,
          note: d.note ?? "",
          request: message,
        });
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

  const accept = () => {
    if (patch) setLastRationale(patch.rationale || null);
    acceptProjectPatch();
  };

  if (!activeProject) {
    return (
      <div className="proj-agent">
        <div className="proj-agent-empty">
          Pick a project (top bar) and open a file to work with the project agent. It edits the whole project — many
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

      {patch && (
        <div className="proj-agent-patch">
          <div className="proj-agent-rationale">{patch.rationale}</div>
          <div className="proj-agent-filelist">
            <div className="proj-agent-filelist-label">
              changes {patch.edits.length} file(s) — click to review the diff:
            </div>
            {patch.edits.map((e) => (
              <button
                key={e.path}
                className="proj-agent-fileitem"
                onClick={() => openEdited(originals, e.path)}
                title="Open this file's diff in the editor"
              >
                {e.path.replace(/^\//, "")}
              </button>
            ))}
          </div>
          <div className="proj-agent-actions">
            <button className="proj-agent-accept" onClick={accept}>
              accept all
            </button>
            <button className="proj-agent-reject" onClick={rejectProjectPatch}>
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
