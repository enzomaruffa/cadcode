import { create } from "zustand";
import { persist } from "zustand/middleware";
import { EDIT_DEBOUNCE_MS, HTTP_URL, WS_URL } from "../config";
import {
  ACCEPT_PATCH,
  AGENT_MESSAGE,
  AGENT_PATCH,
  CHAT,
  CHECKPOINT,
  EDIT,
  ERROR,
  GEOMETRY,
  HISTORY,
  MEASUREMENT,
  PREVIEW_DIFF,
  REDO,
  REJECT_PATCH,
  ROLLBACK,
  RUN,
  SELECT,
  SET_MODE,
  SIMULATION,
  SOURCE,
  STATUS,
  UNDO,
  makeId,
  type AgentMessagePayload,
  type AgentPatchPayload,
  type Commit,
  type Envelope,
  type ErrorPayload,
  type GeometryPayload,
  type HistoryPayload,
  type MeasurementPayload,
  type Param,
  type PhysicalPayload,
  type SelectKind,
  type SimFrame,
  type SimSummary,
  type SimulationPayload,
  type Spec,
  type StatusPayload,
  type TessShapes,
  type ViewMode,
} from "./protocol";

export interface ChatMessage {
  role: "assistant" | "user";
  text: string;
  error?: boolean;
}

export interface TabDoc {
  id: string;
  name: string;
  source: string;
  // Set when the tab is a project file — live-runs then go through the project
  // runner (project on sys.path) instead of the single-buffer kernel.
  origin?: { project: string; kind: string; name: string };
}

const NEW_PART_SKELETON = `from typing import Annotated
from build123d import Box
from lib.params import Range

SIZE: Annotated[float, Range(5, 80)] = 20

# Algebra mode: build objects as expressions, combine with + - & operators.
part = Box(SIZE, SIZE, SIZE)

show(part, name="part", color="#9aa7ff")
`;

type Conn = "connecting" | "open" | "closed";
type RunState = "idle" | "running" | "ok" | "error";

interface ErrorInfo {
  message: string;
  line: number | null;
  traceback: string;
}

interface StoreState {
  conn: Conn;
  source: string;
  docs: TabDoc[];
  activeDocId: string;
  // monotonically bumped whenever new geometry arrives, so the viewport re-renders
  shapes: TessShapes | null;
  geometryRev: number;
  stale: boolean;
  runState: RunState;
  error: ErrorInfo | null;
  stdout: string;
  selection: MeasurementPayload | null;
  specs: Spec[];
  params: Param[];
  viewMode: ViewMode;
  buildAxis: "Z" | "X" | "Y";
  printStats: { faces: number; needs_support: number; build_axis: string; limit: number } | null;
  physical: PhysicalPayload | null;
  // Motion sim (transient — never persisted): the swept frames + playback cursor.
  simFrames: SimFrame[];
  simFrame: number;
  simPlaying: boolean;
  simSummary: SimSummary | null;
  simSpecs: Spec[];
  activeLine: number | null; // editor cursor line (drives highlight glow)
  revealLine: number | null; // line to reveal in the editor (from a face click)
  presentation: boolean; // technical (false) vs presentation/PBR (true) render

  chat: ChatMessage[];
  pendingPatch: AgentPatchPayload | null;
  agentBusy: boolean;
  diffStats: { added: number; removed: number } | null;

  commits: Commit[];
  canUndo: boolean;
  canRedo: boolean;

  // The active project + which file the project-agent runs/renders as the target.
  activeProject: string | null;
  runTarget: { kind: string; name: string } | null;
  // Auto-save status of the active PROJECT file (null for the scratch buffer).
  saveState: "saved" | "saving" | "dirty" | null;

  connect: () => void;
  setSource: (source: string, opts?: { immediate?: boolean }) => void;
  newDoc: () => void;
  openDoc: (name: string, source: string, origin?: { project: string; kind: string; name: string }) => void;
  switchDoc: (id: string) => void;
  closeDoc: (id: string) => void;
  renameDoc: (id: string, name: string) => void;
  runNow: () => void;
  sendSelect: (kind: SelectKind, shapeId: string, index: number) => void;
  clearSelection: () => void;
  setParam: (line: number, name: string, value: number) => void;
  setViewMode: (mode: ViewMode) => void;
  setSimFrame: (frame: number) => void;
  toggleSimPlay: () => void;
  setBuildAxis: (axis: "Z" | "X" | "Y") => void;
  setActiveLine: (line: number | null) => void;
  setRevealLine: (line: number | null) => void;
  togglePresentation: () => void;
  sendChat: (text: string) => void;
  acceptPatch: () => void;
  rejectPatch: () => void;
  previewDiff: () => void;
  checkpoint: (message?: string) => void;
  rollback: (sha: string) => void;
  undo: () => void;
  redo: () => void;

  setRunTarget: (project: string, kind: string, name: string) => void;
  setActiveProject: (project: string | null) => void;
  renderShapes: (shapes: TessShapes | null, specs: Spec[]) => void;
}

let ws: WebSocket | null = null;
let debounceTimer: ReturnType<typeof setTimeout> | null = null;

function send(type: string, payload: Record<string, unknown>) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    const env: Envelope = { type, id: makeId(), payload };
    ws.send(JSON.stringify(env));
  }
}

type Origin = { project: string; kind: string; name: string };

function originRelPath(o: Origin): string {
  return o.kind === "project" ? "project.py" : `${o.kind}s/${o.name}.py`;
}

// Live-run a project file through the project runner (project on sys.path) so its
// `import project` / `from parts.x import x` resolve — the single-buffer kernel
// can't do that. A part has no show() of its own, so preview it by wrapping;
// project.py is constants only, so just confirm it imports.
async function runProjectDoc(origin: Origin, source: string) {
  const rel = originRelPath(origin);
  const body: Record<string, unknown> = { kind: origin.kind, name: origin.name, overrides: { [rel]: source } };
  if (origin.kind === "part") {
    // Run the part's OWN source directly (so error line numbers map to the editor,
    // not to a wrapper) and then call the function to preview it. Showing the
    // return value is guarded so a part that returns nothing (or show()s itself)
    // still previews without a spurious show(None).
    const n = origin.name;
    body.source = `${source}\n\n_preview = ${n}()\nif _preview is not None:\n    show(_preview, name=${JSON.stringify(n)})\n`;
  }
  // kind "project" / "scene": run the file itself (no preview wrapper). project.py
  // is just constants — running it validates it (no geometry); scenes show().
  try {
    const r = await fetch(`${HTTP_URL}/projects/${origin.project}/run`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const d: {
      ok?: boolean;
      shapes?: TessShapes;
      specs?: Spec[];
      params?: Param[];
      error?: string;
      error_line?: number | null;
    } = await r.json();
    if (d.ok && d.shapes) {
      useStore.getState().renderShapes(d.shapes, d.specs ?? []);
      useStore.setState({ params: d.params ?? [] }); // sliders for the edited project file
    } else if (d.ok) {
      useStore.setState({ error: null, runState: "ok", params: d.params ?? [] }); // ran, no geometry (project.py)
    } else {
      let message = d.error ?? "run failed";
      // A part is imported as a module, so show()/require() aren't in scope there.
      // If a part calls them it's really a scene — say so instead of a cryptic
      // "line 1: NameError: name 'show' is not defined".
      if (origin.kind === "part" && /name '(show|require)' is not defined/.test(message)) {
        message =
          "A part is a function that returns a shape and can't call show()/require() — those are for scenes. " +
          "Wrap the body in `def name(...): … return part` (no show/require), or save this as a scene instead.";
      }
      useStore.setState({
        error: { message, line: d.error_line ?? null, traceback: "" },
        runState: "error",
      });
    }
  } catch (e) {
    useStore.setState({ error: { message: String(e), line: null, traceback: "" }, runState: "error" });
  }
}

// Persist a project file to disk (autosave). reload:false — the project runner
// always re-materializes from disk + the live buffer, so no kernel recycle is
// needed per keystroke. Fire-and-forget; drives the saveState indicator.
async function saveProjectDoc(origin: Origin, source: string) {
  useStore.setState({ saveState: "saving" });
  try {
    await fetch(`${HTTP_URL}/projects/${origin.project}/file`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind: origin.kind, name: origin.name, source, reload: false }),
    });
    // Only clear to "saved" if nothing newer is pending.
    if (useStore.getState().saveState === "saving") useStore.setState({ saveState: "saved" });
  } catch {
    useStore.setState({ saveState: "dirty" });
  }
}

export const useStore = create<StoreState>()(
  persist(
    (set, get) => ({
      conn: "connecting",
      source: "",
      docs: [],
      activeDocId: "",
      shapes: null,
      geometryRev: 0,
      stale: false,
      runState: "idle",
      error: null,
      stdout: "",
      selection: null,
      specs: [],
      params: [],
      viewMode: "technical",
      buildAxis: "Z",
      printStats: null,
      physical: null,
      simFrames: [],
      simFrame: 0,
      simPlaying: false,
      simSummary: null,
      simSpecs: [],
      activeLine: null,
      revealLine: null,
      presentation: false,
      chat: [],
      pendingPatch: null,
      agentBusy: false,
      diffStats: null,
      commits: [],
      canUndo: false,
      canRedo: false,
      activeProject: null,
      runTarget: null,
      saveState: null,

      connect: () => {
        // Guard against React StrictMode's double-invoke opening two sockets.
        if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
          return;
        }
        // Fetch the starter buffer, then open the socket.
        fetch(`${HTTP_URL}/default-source`)
          .then((r) => r.json())
          .then((d: { source: string }) => {
            if (get().docs.length === 0) {
              const id = makeId();
              set({ docs: [{ id, name: "part 1", source: d.source }], activeDocId: id, source: d.source });
            }
          })
          .catch(() => void 0);

        const socket = new WebSocket(WS_URL);
        ws = socket;
        set({ conn: "connecting" });

        socket.onopen = () => {
          set({ conn: "open" });
          // Re-render the restored buffer on (re)connect. A project file must go
          // through the project runner (project on sys.path, sandbox off) — sending
          // it over the single-buffer WS would sandbox-reject `from project import`.
          const active = get().docs.find((d) => d.id === get().activeDocId);
          if (active?.origin) void runProjectDoc(active.origin, active.source);
          else if (get().source) send(EDIT, { source: get().source });
        };
        socket.onclose = () => {
          set({ conn: "closed" });
          // simple auto-reconnect
          setTimeout(() => get().connect(), 1500);
        };
        socket.onmessage = (ev) => {
          let env: Envelope;
          try {
            env = JSON.parse(ev.data as string);
          } catch {
            return;
          }
          switch (env.type) {
            case GEOMETRY: {
              const p = env.payload as unknown as GeometryPayload;
              const incomingMode = p.mode ?? "technical";
              // Physical mode carries no geometry — only the readout. Update just
              // `physical` and leave shapes/geometryRev untouched so the model
              // stays on screen (the overlay draws on top of technical shading).
              if (incomingMode === "physical") {
                set({ physical: (p.physical as PhysicalPayload | null) ?? null });
                break;
              }
              set((s) => ({
                shapes: p.shapes,
                geometryRev: s.geometryRev + 1,
                stale: !!p.stale,
                stdout: p.stdout ?? s.stdout,
                // A stale render (a failed compile re-showing the last good geometry)
                // carries no specs/params — keep the previous ones so sliders + specs
                // don't flap on every transient error while typing.
                specs: p.stale ? s.specs : incomingMode === "technical" ? (p.specs ?? []) : s.specs,
                params: p.stale ? s.params : incomingMode === "technical" ? (p.params ?? []) : s.params,
                printStats:
                  incomingMode === "printability"
                    ? ((p.print_stats as unknown as StoreState["printStats"]) ?? null)
                    : s.printStats,
                diffStats:
                  incomingMode === "geomdiff"
                    ? ((p.print_stats as unknown as { added: number; removed: number }) ?? null)
                    : s.diffStats,
              }));
              // Keep the heatmap live: if we're in printability mode but just got a
              // fresh technical render (e.g. after an edit), re-request the overlay.
              const vm = get().viewMode;
              if (vm === "printability" && incomingMode === "technical") {
                send(SET_MODE, { mode: "printability", build_axis: get().buildAxis });
              } else if (vm === "highlight" && incomingMode === "technical") {
                send(SET_MODE, { mode: "highlight" });
              } else if (vm === "physical" && incomingMode === "technical") {
                // Re-derive the readout on every edit while physical mode is open.
                send(SET_MODE, { mode: "physical" });
              } else if (vm === "motion" && incomingMode === "technical") {
                // Re-sweep the mechanism on every edit while motion mode is open.
                send(SET_MODE, { mode: "motion" });
              }
              break;
            }
            case ERROR: {
              const p = env.payload as unknown as ErrorPayload;
              set({ error: { message: p.message, line: p.line, traceback: p.traceback } });
              break;
            }
            case STATUS: {
              const p = env.payload as unknown as StatusPayload;
              set({ runState: p.state });
              if (p.state === "ok" || p.state === "running") set({ error: null });
              break;
            }
            case MEASUREMENT: {
              set({ selection: env.payload as unknown as MeasurementPayload });
              break;
            }
            case AGENT_MESSAGE: {
              const p = env.payload as unknown as AgentMessagePayload;
              set((s) => ({ chat: [...s.chat, { role: p.role, text: p.text, error: p.error }], agentBusy: false }));
              break;
            }
            case AGENT_PATCH: {
              set({ pendingPatch: env.payload as unknown as AgentPatchPayload, agentBusy: false });
              break;
            }
            case HISTORY: {
              const p = env.payload as unknown as HistoryPayload;
              // Edits/accepts push only the flags (no commits) — keep the existing list.
              set((s) => ({ commits: p.commits ?? s.commits, canUndo: !!p.can_undo, canRedo: !!p.can_redo }));
              break;
            }
            case SOURCE: {
              // Authoritative buffer push from the backend (rollback/undo/redo).
              const src = (env.payload as { source: string }).source;
              set((s) => ({
                source: src,
                docs: s.docs.map((d) => (d.id === s.activeDocId ? { ...d, source: src } : d)),
              }));
              break;
            }
            case SIMULATION: {
              const p = env.payload as unknown as SimulationPayload;
              set({
                simFrames: p.frames ?? [],
                simSummary: p.summary ?? null,
                simSpecs: p.specs ?? [],
                simFrame: 0,
                simPlaying: (p.frames?.length ?? 0) > 1,
              });
              break;
            }
            default:
              break;
          }
        };
      },

      setSource: (source, opts) => {
        // keep the active tab's buffer in sync
        set((s) => ({
          source,
          docs: s.docs.map((d) => (d.id === s.activeDocId ? { ...d, source } : d)),
        }));
        if (debounceTimer) clearTimeout(debounceTimer);
        const active = get().docs.find((d) => d.id === get().activeDocId);
        const origin = active?.origin;
        // Project files run through the project runner AND auto-save to disk; plain
        // buffers run over the WS (persisted in localStorage, not a file).
        let fire: () => void;
        if (origin) {
          set({ saveState: "dirty" });
          fire = () => {
            void runProjectDoc(origin, source);
            void saveProjectDoc(origin, source);
          };
        } else {
          fire = () => send(EDIT, { source, debounced: true });
        }
        if (opts?.immediate) fire();
        else debounceTimer = setTimeout(fire, EDIT_DEBOUNCE_MS);
      },

      newDoc: () => {
        const id = makeId();
        set((s) => ({ docs: [...s.docs, { id, name: `part ${s.docs.length + 1}`, source: NEW_PART_SKELETON }] }));
        get().switchDoc(id);
      },

      openDoc: (name, source, origin) => {
        // If this project file is already open, just focus it (don't duplicate tabs).
        if (origin) {
          const existing = get().docs.find(
            (d) =>
              d.origin &&
              d.origin.project === origin.project &&
              d.origin.kind === origin.kind &&
              d.origin.name === origin.name,
          );
          if (existing) {
            get().switchDoc(existing.id);
            return;
          }
        }
        const id = makeId();
        set((s) => ({ docs: [...s.docs, { id, name: name || `part ${s.docs.length + 1}`, source, origin }] }));
        get().switchDoc(id);
      },

      switchDoc: (id) => {
        const doc = get().docs.find((d) => d.id === id);
        if (!doc) return;
        if (debounceTimer) clearTimeout(debounceTimer);
        set({
          activeDocId: id,
          source: doc.source,
          selection: null,
          error: null,
          saveState: doc.origin ? "saved" : null,
        });
        if (doc.origin)
          void runProjectDoc(doc.origin, doc.source); // project file → project runner
        else send(EDIT, { source: doc.source }); // plain buffer → single-buffer kernel
      },

      closeDoc: (id) => {
        const { docs, activeDocId } = get();
        if (docs.length <= 1) return; // keep at least one tab
        const remaining = docs.filter((d) => d.id !== id);
        set({ docs: remaining });
        if (id === activeDocId) get().switchDoc(remaining[0].id);
      },

      renameDoc: (id, name) =>
        set((s) => ({ docs: s.docs.map((d) => (d.id === id ? { ...d, name: name || d.name } : d)) })),

      runNow: () => {
        if (debounceTimer) clearTimeout(debounceTimer);
        const active = get().docs.find((d) => d.id === get().activeDocId);
        if (active?.origin)
          void runProjectDoc(active.origin, active.source); // project file → project runner
        else send(RUN, {});
      },

      sendSelect: (kind, shapeId, index) => {
        send(SELECT, { kind, shape_id: shapeId, index });
      },

      clearSelection: () => set({ selection: null }),

      setViewMode: (mode) => {
        const prev = get().viewMode;
        set({ viewMode: mode });
        // Leaving motion: stop playback (the technical re-render restores base pose).
        if (prev === "motion" && mode !== "motion") set({ simPlaying: false });
        if (mode === "printability") send(SET_MODE, { mode: "printability", build_axis: get().buildAxis });
        else if (mode === "highlight") send(SET_MODE, { mode: "highlight" });
        else if (mode === "physical") send(SET_MODE, { mode: "physical" });
        else if (mode === "motion") send(SET_MODE, { mode: "motion" });
        else {
          set({ printStats: null });
          send(SET_MODE, { mode: "technical" });
        }
      },

      setSimFrame: (frame) =>
        set((s) => {
          const n = s.simFrames.length;
          if (n === 0) return { simFrame: 0 };
          return { simFrame: ((frame % n) + n) % n };
        }),
      toggleSimPlay: () => set((s) => ({ simPlaying: !s.simPlaying })),

      setActiveLine: (line) => set({ activeLine: line }),
      setRevealLine: (line) => set({ revealLine: line }),
      togglePresentation: () => set((s) => ({ presentation: !s.presentation })),

      setBuildAxis: (axis) => {
        set({ buildAxis: axis });
        if (get().viewMode === "printability") send(SET_MODE, { mode: "printability", build_axis: axis });
      },

      setParam: (line, name, value) => {
        const lines = get().source.split("\n");
        const idx = line - 1;
        if (idx < 0 || idx >= lines.length) return;
        // Replace the number after the assignment `=`, tolerating a type annotation
        // in between (e.g. `WIDTH: Annotated[float, Range(20, 160)] = 80`). The lazy
        // `.*?` stops at the first `=`, which is the assignment (Range() has no `=`).
        const re = new RegExp(`^(\\s*${name}\\b.*?=\\s*)(-?\\d+(?:\\.\\d+)?)(.*)$`);
        const replaced = lines[idx].replace(re, (_m, pre, _num, rest) => `${pre}${value}${rest}`);
        if (replaced === lines[idx]) return; // no match — bail rather than corrupt
        lines[idx] = replaced;
        get().setSource(lines.join("\n"));
      },

      sendChat: (text) => {
        const t = text.trim();
        if (!t) return;
        set((s) => ({ chat: [...s.chat, { role: "user", text: t }], agentBusy: true }));
        send(CHAT, { message: t });
      },

      acceptPatch: () => {
        const patch = get().pendingPatch;
        if (!patch) return;
        // Update the editor to the agent's source (the backend applies + re-runs).
        set((s) => ({
          source: patch.new_source,
          docs: s.docs.map((d) => (d.id === s.activeDocId ? { ...d, source: patch.new_source } : d)),
          pendingPatch: null,
          diffStats: null,
          chat: [...s.chat, { role: "assistant", text: "✓ patch accepted" }],
        }));
        send(ACCEPT_PATCH, { new_source: patch.new_source });
      },

      rejectPatch: () => {
        set((s) => ({
          pendingPatch: null,
          diffStats: null,
          chat: [...s.chat, { role: "assistant", text: "✗ patch rejected" }],
        }));
        send(REJECT_PATCH, {});
        send(RUN, {}); // restore the normal render (in case a diff was being previewed)
      },

      previewDiff: () => {
        const patch = get().pendingPatch;
        if (patch) send(PREVIEW_DIFF, { new_source: patch.new_source });
      },

      checkpoint: (message) => send(CHECKPOINT, { message: message || "checkpoint" }),
      rollback: (sha) => send(ROLLBACK, { to: sha }),
      undo: () => send(UNDO, {}),
      redo: () => send(REDO, {}),

      setRunTarget: (project, kind, name) => set({ activeProject: project, runTarget: { kind, name } }),
      setActiveProject: (project) => set({ activeProject: project, runTarget: null }),
      renderShapes: (shapes, specs) =>
        set((s) => ({ shapes, geometryRev: s.geometryRev + 1, specs, stale: false, error: null, runState: "ok" })),
    }),
    {
      name: "cadcode.store",
      version: 1,
      // Persist the user's work + conversation so a refresh restores everything.
      // Transient/derived state (socket, geometry, run status) is not persisted.
      partialize: (s) => ({
        docs: s.docs,
        activeDocId: s.activeDocId,
        source: s.source,
        chat: s.chat,
        viewMode: s.viewMode,
        buildAxis: s.buildAxis,
        presentation: s.presentation,
        activeProject: s.activeProject,
        runTarget: s.runTarget,
      }),
    },
  ),
);

// Expose for debugging / E2E.
(self as unknown as { __store: typeof useStore }).__store = useStore;
