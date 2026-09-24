import type { Config } from "tailwindcss";

import { ANDON_COLORS } from "./src/lib/stateStyle";

// The andon palette's one source of truth is src/lib/stateStyle.ts; this just exposes it as
// Tailwind classes (bg-andon-fault, etc.) for contexts that can use them.
const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: { andon: ANDON_COLORS },
      fontFamily: {
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
    },
  },
  plugins: [],
};

export default config;
