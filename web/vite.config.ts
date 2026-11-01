import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
import { defineConfig, loadEnv, type Plugin } from "vite";
import { devFixtures } from "./dev-fixtures/plugin";

const webRoot = fileURLToPath(new URL(".", import.meta.url));

/**
 * Link previews (Slack, Devpost, X) need an absolute og:image URL. index.html carries the site-
 * relative "/og-image.jpg"; when the build knows the site's address (VITE_SITE_URL, or Vercel's
 * VERCEL_PROJECT_PRODUCTION_URL) the image URLs are made absolute.
 */
function absoluteSocialImage(siteUrl: string): Plugin {
  return {
    name: "showtell-absolute-social-image",
    transformIndexHtml(html) {
      if (!siteUrl) return html;
      return html.replace(/content="\/og-image\.jpg"/g, `content="${siteUrl}/og-image.jpg"`);
    },
  };
}

export default defineConfig(({ command, mode }) => {
  const env = loadEnv(mode, webRoot, "");
  // Sample data + mock API for UI work without the backend: dev server only, never in a build.
  const fixture = command === "serve" && env.VITE_DEV_FIXTURE === "1";
  const target = env.VITE_PROXY_TARGET || "http://localhost:8000";
  const siteUrl = (
    env.VITE_SITE_URL ||
    (env.VERCEL_PROJECT_PRODUCTION_URL ? `https://${env.VERCEL_PROJECT_PRODUCTION_URL}` : "")
  ).replace(/\/+$/, "");

  return {
    plugins: [react(), absoluteSocialImage(siteUrl), ...(fixture ? [devFixtures()] : [])],
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
