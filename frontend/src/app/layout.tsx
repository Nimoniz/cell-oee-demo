import type { Metadata } from "next";
import Link from "next/link";

import { runtimeConfigScript } from "@/lib/runtimeConfig";

import { Providers } from "./providers";
import "./globals.css";

export const metadata: Metadata = {
  title: "Cell OEE Demo",
  description: "TRS en direct d'une cellule de soudure robotisée",
};

const NAV = [
  { href: "/live", label: "Vue live" },
  { href: "/operator", label: "Qualification opérateur" },
  { href: "/analysis", label: "Analyse" },
];

// Without this, Next statically prerenders the layout at `next build` time (nothing else in the
// tree forces dynamic rendering), baking in whatever $API_URL happened to be set then — exactly
// the "frozen at build time" outcome runtime config is meant to avoid. Forcing dynamic rendering
// makes this Server Component run per request, in the container that is actually serving it.
export const dynamic = "force-dynamic";

export default function RootLayout({ children }: { children: React.ReactNode }) {
  // Server Component: runs on every request in the running container, so this reads the
  // container's own $API_URL — never a value frozen at `next build` time.
  const script = runtimeConfigScript(process.env.API_URL);

  return (
    <html lang="fr">
      <head>
        {/* Must run before any other script touches the API. */}
        <script dangerouslySetInnerHTML={{ __html: script }} />
      </head>
      <body>
        <Providers>
          <div className="flex min-h-screen flex-col">
            <header className="border-b border-slate-800 bg-slate-900/60 px-4 py-2">
              <nav className="flex items-center gap-1">
                <span className="mr-4 font-semibold text-slate-300">Cell OEE Demo</span>
                {NAV.map((item) => (
                  <Link
                    key={item.href}
                    href={item.href}
                    className="touch-target flex items-center rounded px-3 py-2 text-sm text-slate-300 hover:bg-slate-800 hover:text-white"
                  >
                    {item.label}
                  </Link>
                ))}
              </nav>
            </header>
            <main className="flex-1">{children}</main>
            <footer className="border-t border-slate-800 px-4 py-1 text-center text-xs text-slate-500">
              Heure usine (simulée) — jamais convertie au fuseau du navigateur
            </footer>
          </div>
        </Providers>
      </body>
    </html>
  );
}
