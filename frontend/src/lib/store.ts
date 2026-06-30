import { create } from "zustand";
import { EDIT_DEBOUNCE_MS, HTTP_URL, WS_URL } from "../config";
import {
  EDIT,
  ERROR,
  GEOMETRY,
  RUN,
  STATUS,
  makeId,
  type Envelope,
  type ErrorPayload,
  type GeometryPayload,
  type StatusPayload,
  type TessShapes,
} from "./protocol";

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

  connect: () => void;
  setSource: (source: string, opts?: { immediate?: boolean }) => void;
  runNow: () => void;
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
}));

// Expose for debugging / E2E.
(self as unknown as { __store: typeof useStore }).__store = useStore;
