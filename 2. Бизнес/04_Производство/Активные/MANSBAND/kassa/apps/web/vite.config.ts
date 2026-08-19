import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Прод-сборка под nginx: статика в dist/, ассеты в assets/.
// /api и /ws проксируются nginx-ом на бэкенд; в dev — vite proxy на localhost:3001.
export default defineConfig({
  plugins: [react()],
  base: "/",
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:3001",
        changeOrigin: true,
      },
      "/ws": {
        target: "ws://localhost:3001",
        ws: true,
      },
    },
  },
  build: {
    outDir: "dist",
    assetsDir: "assets",
    sourcemap: false,
  },
});
