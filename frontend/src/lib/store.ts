import { create } from "zustand";
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
  type SelectKind,
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
}

const NEW_PART_SKELETON = `from typing import Annotated
from build123d import BuildPart, Box
from lib.params import Range

SIZE: Annotated[float, Range(5, 80)] = 20

with BuildPart() as part:
    Box(SIZE, SIZE, SIZE)

show(part.part, name="part", color="#9aa7ff")
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

  connect: () => void;
  setSource: (source: string, opts?: { immediate?: boolean }) => void;
  newDoc: () => void;
  openDoc: (name: string, source: string) => void;
  switchDoc: (id: string) => void;
  closeDoc: (id: string) => void;
  renameDoc: (id: string, name: string) => void;
  runNow: () => void;
  sendSelect: (kind: SelectKind, shapeId: string, index: number) => void;
  clearSelection: () => void;
  setParam: (line: number, name: string, value: number) => void;
  setViewMode: (mode: ViewMode) => void;
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
}

let ws: WebSocket | null = null;
let debounceTimer: ReturnType<typeof setTimeout> | null = null;

function send(type: string, payload: Record<string, unknown>) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    const env: Envelope = { type, id: makeId(), payload };
    ws.send(JSON.stringify(env));
  }
}

export const useStore = create<StoreState>((set, get) => ({
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

    socket.onopen = () => set({ conn: "open" });
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
          set((s) => ({
            shapes: p.shapes,
            geometryRev: s.geometryRev + 1,
            stale: !!p.stale,
            stdout: p.stdout ?? s.stdout,
            specs: incomingMode === "technical" ? (p.specs ?? []) : s.specs,
            params: incomingMode === "technical" ? (p.params ?? []) : s.params,
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
          set({ commits: p.commits ?? [], canUndo: !!p.can_undo, canRedo: !!p.can_redo });
          break;
        }
        case SOURCE: {
          // Authoritative buffer push from the backend (rollback/undo/redo).
          const src = (env.payload as { source: string }).source;
          set((s) => ({ source: src, docs: s.docs.map((d) => (d.id === s.activeDocId ? { ...d, source: src } : d)) }));
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
    const fire = () => send(EDIT, { source, debounced: true });
    if (opts?.immediate) fire();
    else debounceTimer = setTimeout(fire, EDIT_DEBOUNCE_MS);
  },

  newDoc: () => {
    const id = makeId();
    set((s) => ({ docs: [...s.docs, { id, name: `part ${s.docs.length + 1}`, source: NEW_PART_SKELETON }] }));
    get().switchDoc(id);
  },

  openDoc: (name, source) => {
    const id = makeId();
    set((s) => ({ docs: [...s.docs, { id, name: name || `part ${s.docs.length + 1}`, source }] }));
    get().switchDoc(id);
  },

  switchDoc: (id) => {
    const doc = get().docs.find((d) => d.id === id);
    if (!doc) return;
    if (debounceTimer) clearTimeout(debounceTimer);
    set({ activeDocId: id, source: doc.source, selection: null, error: null });
    send(EDIT, { source: doc.source }); // run the part for this tab
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
    send(RUN, {});
  },

  sendSelect: (kind, shapeId, index) => {
    send(SELECT, { kind, shape_id: shapeId, index });
  },

  clearSelection: () => set({ selection: null }),

  setViewMode: (mode) => {
    set({ viewMode: mode });
    if (mode === "printability") send(SET_MODE, { mode: "printability", build_axis: get().buildAxis });
    else if (mode === "highlight") send(SET_MODE, { mode: "highlight" });
    else {
      set({ printStats: null });
      send(SET_MODE, { mode: "technical" });
    }
  },

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
    // Replace the number after `NAME =` on that line, preserving the rest.
    const re = new RegExp(`^(\\s*${name}\\s*=\\s*)(-?\\d+(?:\\.\\d+)?)(.*)$`);
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
}));

// Expose for debugging / E2E.
(self as unknown as { __store: typeof useStore }).__store = useStore;
