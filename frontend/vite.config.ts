import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@cadcode/viewer": fileURLToPath(new URL("../viewer/src/index.ts", import.meta.url)),
    },
    // The viewer package has its own node_modules; dedupe so the bundle ships a
    // single copy of these (one React instance, one three instance).
    dedupe: ["react", "react-dom", "three"],
  },
  server: {
    port: 5173,
    strictPort: true,
    // The viewer package lives outside frontend/ — allow Vite to serve it.
    fs: { allow: [".."] },
  },
});
