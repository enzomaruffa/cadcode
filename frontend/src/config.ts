// Backend endpoints. Override with VITE_WS_URL / VITE_HTTP_URL at build time.
const HTTP_DEFAULT = "http://127.0.0.1:8787";
const WS_DEFAULT = "ws://127.0.0.1:8787/ws";

export const HTTP_URL: string = import.meta.env.VITE_HTTP_URL ?? HTTP_DEFAULT;
export const WS_URL: string = import.meta.env.VITE_WS_URL ?? WS_DEFAULT;

// Debounce for live auto-run on edit (plan §3: ~250ms).
export const EDIT_DEBOUNCE_MS = 250;
