/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The bundle is committed to src/webdisplay/static so neither the Pi nor the
// image build needs Node. Rebuild it with `npm run build` in ui/.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../src/webdisplay/static",
    emptyOutDir: true,
    target: "es2019",
    cssCodeSplit: false,
    assetsInlineLimit: 0,
  },
  server: {
    proxy: {
      "/api": { target: "https://localhost:8443", secure: false },
      "/health": { target: "https://localhost:8443", secure: false },
    },
  },
  test: { environment: "jsdom", globals: true },
});
