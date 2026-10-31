import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
import { defineConfig, loadEnv } from "vite";
import { devFixtures } from "./dev-fixtures/plugin";

const webRoot = fileURLToPath(new URL(".", import.meta.url));

export default defineConfig(({ command, mode }) => {
  const env = loadEnv(mode, webRoot, "");
  // Sample data + mock API for UI work without the backend: dev server only, never in a build.
  const fixture = command === "serve" && env.VITE_DEV_FIXTURE === "1";
  const target = env.VITE_PROXY_TARGET || "http://localhost:8000";

  return {
    plugins: [react(), ...(fixture ? [devFixtures()] : [])],
    server: {
      port: 5173,
      // `npm run dev` talks to a local `uvicorn showtell.api.app:app` through this proxy (SSE included).
      proxy: fixture
        ? undefined
        : {
            "/api": { target, changeOrigin: true },
            "/media": { target, changeOrigin: true },
          },
    },
    preview: { port: 4173 },
    build: {
      target: "es2022",
      sourcemap: false,
    },
  };
});
