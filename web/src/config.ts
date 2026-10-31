/**
 * Runtime configuration from Vite env vars, and the demo/live mode.
 *
 * - demo: replays recorded runs from public/demo/ (no backend). Default for production builds
 *   (.env.production sets VITE_DEMO=1).
 * - live: talks to the FastAPI backend at VITE_API_URL (or same origin; `npm run dev` proxies
 *   /api and /media). "?live=1" switches to live, "?demo=1" back; the choice is remembered for
 *   the browser tab.
 */

export type Mode = "demo" | "live";

const env = import.meta.env;

/** Backend origin without a trailing slash; "" = same origin. */
export const API_URL: string = String(env.VITE_API_URL ?? "")
  .trim()
  .replace(/\/+$/, "");

/** Dev-only sample data (web/dev-fixtures), served by the Vite dev server. Never in a build. */
export const IS_FIXTURE: boolean = env.DEV && env.VITE_DEV_FIXTURE === "1";

export const DEMO_BY_DEFAULT: boolean = env.VITE_DEMO === "1";

/** Live mode needs a backend: an explicit URL, the dev proxy, or a non-demo build served next to the API. */
export const LIVE_AVAILABLE: boolean = API_URL !== "" || env.DEV || !DEMO_BY_DEFAULT;

/** Where recorded runs live: public/demo/ in builds, web/dev-fixtures/ in fixture mode. */
export const DEMO_BASE: string = IS_FIXTURE ? "/__dev-fixtures/" : `${env.BASE_URL}demo/`;

export const FIXTURE_LABEL = "Sample data - not a real run";

const MODE_KEY = "showtell.mode";

function readStoredMode(): Mode | null {
  try {
    const v = window.sessionStorage.getItem(MODE_KEY);
    return v === "demo" || v === "live" ? v : null;
  } catch {
    return null;
  }
}

export function storeMode(mode: Mode): void {
  try {
    window.sessionStorage.setItem(MODE_KEY, mode);
  } catch {
    // storage unavailable (private window, blocked site data): the mode just isn't remembered
  }
}

export function resolveInitialMode(): Mode {
  const q = new URLSearchParams(window.location.search);
  if (q.get("live") === "1" && LIVE_AVAILABLE) {
    storeMode("live");
    return "live";
  }
  if (q.get("demo") === "1" || q.get("live") === "0") {
    storeMode("demo");
    return "demo";
  }
  const stored = readStoredMode();
  if (stored === "demo" || (stored === "live" && LIVE_AVAILABLE)) return stored;
  if (IS_FIXTURE) return "demo";
  return DEMO_BY_DEFAULT || !LIVE_AVAILABLE ? "demo" : "live";
}

/** Human-readable backend location for status chips and error messages. */
export function backendLabel(): string {
  if (API_URL) {
    try {
      return new URL(API_URL).host;
    } catch {
      return API_URL;
    }
  }
  if (IS_FIXTURE) return "mock API (sample data)";
  return env.DEV ? "dev proxy" : window.location.host;
}
