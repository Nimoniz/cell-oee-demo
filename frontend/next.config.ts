import type { NextConfig } from "next";

// Standalone output: a self-contained server bundle for the Dockerfile. No env var is baked in
// at build time — the API URL is read server-side, at request time, from process.env (see
// src/app/layout.tsx), so one built image runs against whatever $API_URL the container gets.
const nextConfig: NextConfig = {
  output: "standalone",
  reactStrictMode: true,
  // The dev-mode indicator floats bottom-left and, on a short/narrow viewport (tablet), sits on
  // top of the "Arrêt en cours" card and makes its text unreadable. It is not part of the UI.
  devIndicators: false,
};

export default nextConfig;
