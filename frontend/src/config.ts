// Backend endpoints.
//  - dev (Vite on :5173): talk to the standalone backend on :8787
//  - prod (served by the backend itself): same origin
// Override either with VITE_WS_URL / VITE_HTTP_URL at build time.

function defaults(): { http: string; ws: string } {
  if (import.meta.env.DEV) {
    return { http: "http://127.0.0.1:8787", ws: "ws://127.0.0.1:8787/ws" };
  }
  const wsProto = location.protocol === "https:" ? "wss:" : "ws:";
  return { http: location.origin, ws: `${wsProto}//${location.host}/ws` };
}

const d = defaults();
export const HTTP_URL: string = import.meta.env.VITE_HTTP_URL ?? d.http;
export const WS_URL: string = import.meta.env.VITE_WS_URL ?? d.ws;

// Debounce for live auto-run on edit (plan §3: ~250ms).
export const EDIT_DEBOUNCE_MS = 250;
