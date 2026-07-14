import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";

// Built assets are served by the FastAPI daemon's existing /static mount, so
// the bundle lives inside the package's static dir under dist/.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  base: "/static/dist/",
  build: {
    outDir: "../src/whisper_to_me/static/dist",
    emptyOutDir: true,
  },
  server: {
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8737",
        ws: true,
      },
    },
  },
});
