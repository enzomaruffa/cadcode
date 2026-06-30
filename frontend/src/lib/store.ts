import { create } from "zustand";
import { EDIT_DEBOUNCE_MS, HTTP_URL, WS_URL } from "../config";
import {
  ACCEPT_PATCH,
  AGENT_MESSAGE,
  AGENT_PATCH,
  CHAT,
  EDIT,
  ERROR,
  GEOMETRY,
  MEASUREMENT,
  REJECT_PATCH,
  RUN,
  SELECT,
  STATUS,
  makeId,
  type AgentMessagePayload,
  type AgentPatchPayload,
  type Envelope,
  type ErrorPayload,
  type GeometryPayload,
  type MeasurementPayload,
  type SelectKind,
  type StatusPayload,
  type TessShapes,
} from "./protocol";

export interface ChatMessage {
  role: "assistant" | "user";
  text: string;
  error?: boolean;
}

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
  // monotonically bumped whenever new geometry arrives, so the viewport re-renders
  shapes: TessShapes | null;
  geometryRev: number;
  stale: boolean;
  runState: RunState;
  error: ErrorInfo | null;
  stdout: string;
  selection: MeasurementPayload | null;

  chat: ChatMessage[];
  pendingPatch: AgentPatchPayload | null;
  agentBusy: boolean;

  connect: () => void;
  setSource: (source: string, opts?: { immediate?: boolean }) => void;
  runNow: () => void;
  sendSelect: (kind: SelectKind, shapeId: string, index: number) => void;
  clearSelection: () => void;
  sendChat: (text: string) => void;
  acceptPatch: () => void;
  rejectPatch: () => void;
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
  shapes: null,
  geometryRev: 0,
  stale: false,
  runState: "idle",
  error: null,
  stdout: "",
  selection: null,
  chat: [],
  pendingPatch: null,
  agentBusy: false,

  connect: () => {
    // Guard against React StrictMode's double-invoke opening two sockets.
    if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
      return;
    }
    // Fetch the starter buffer, then open the socket.
    fetch(`${HTTP_URL}/default-source`)
      .then((r) => r.json())
      .then((d: { source: string }) => {
        if (!get().source) set({ source: d.source });
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
          set((s) => ({
            shapes: p.shapes,
            geometryRev: s.geometryRev + 1,
            stale: !!p.stale,
            stdout: p.stdout ?? s.stdout,
          }));
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
        default:
          break;
      }
    };
  },

  setSource: (source, opts) => {
    set({ source });
    if (debounceTimer) clearTimeout(debounceTimer);
    const fire = () => send(EDIT, { source, debounced: true });
    if (opts?.immediate) fire();
    else debounceTimer = setTimeout(fire, EDIT_DEBOUNCE_MS);
  },

  runNow: () => {
    if (debounceTimer) clearTimeout(debounceTimer);
    send(RUN, {});
  },

  sendSelect: (kind, shapeId, index) => {
    send(SELECT, { kind, shape_id: shapeId, index });
  },

  clearSelection: () => set({ selection: null }),

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
      pendingPatch: null,
      chat: [...s.chat, { role: "assistant", text: "✓ patch accepted" }],
    }));
    send(ACCEPT_PATCH, { new_source: patch.new_source });
  },

  rejectPatch: () => {
    set((s) => ({ pendingPatch: null, chat: [...s.chat, { role: "assistant", text: "✗ patch rejected" }] }));
    send(REJECT_PATCH, {});
  },
}));

// Expose for debugging / E2E.
(self as unknown as { __store: typeof useStore }).__store = useStore;
