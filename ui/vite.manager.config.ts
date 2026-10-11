/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The manager UI is committed to src/webdisplay_manager/static so the PC service needs no Node.
// Rebuild with `npm run build:manager`.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../src/webdisplay_manager/static",
    emptyOutDir: true,
    target: "es2019",
    cssCodeSplit: false,
    assetsInlineLimit: 0,
    rollupOptions: { input: "manager.html" },
  },
  server: { proxy: { "/api": "http://127.0.0.1:8765" } },
});
