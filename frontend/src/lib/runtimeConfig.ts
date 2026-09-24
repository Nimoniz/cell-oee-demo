/**
 * The API's URL is read at container start, not baked in at `next build` — the same built image
 * must be runnable against any `$API_URL`. `RootLayout` (a Server Component, so it runs on every
 * request inside the running container) reads `process.env.API_URL` and injects it as
 * `window.__ENV__` before any other script runs; this module is how client code reads it back.
 */

export interface RuntimeConfig {
  apiUrl: string; // e.g. "http://localhost:8000", no trailing slash
  wsUrl: string; // e.g. "ws://localhost:8000"
}

declare global {
  interface Window {
    __ENV__?: RuntimeConfig;
  }
}

export function runtimeConfigScript(apiUrlEnv: string | undefined): string {
  const apiUrl = (apiUrlEnv ?? "http://localhost:8000").replace(/\/+$/, "");
  const wsUrl = apiUrl.replace(/^http/, "ws");
  const cfg: RuntimeConfig = { apiUrl, wsUrl };
  // apiUrlEnv is an infra setting (docker-compose's $API_URL), not user input; escaping "<" is
  // just defence in depth against an operator-set value that happens to contain "</script>".
  return `window.__ENV__=${JSON.stringify(cfg).replace(/</g, "\\u003c")};`;
}

export function getRuntimeConfig(): RuntimeConfig {
  if (typeof window === "undefined") {
    throw new Error("getRuntimeConfig() must run in the browser");
  }
  const cfg = window.__ENV__;
  if (!cfg) {
    throw new Error("runtime config missing: window.__ENV__ was not injected by the layout");
  }
  return cfg;
}
