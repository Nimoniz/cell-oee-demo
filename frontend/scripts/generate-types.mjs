#!/usr/bin/env node
/**
 * Regenerates src/lib/api/types.gen.ts from the backend's OpenAPI schema — the single source of
 * truth for the front's API types. Run via `npm run generate:types`; `npm run check` fails if
 * this drifts from what is committed (see the "types are up to date" test).
 */
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const frontendRoot = path.resolve(here, "..");
const backendRoot = path.resolve(frontendRoot, "..", "backend");

function findPython() {
  const candidates = [
    path.join(backendRoot, ".venv", "Scripts", "python.exe"),
    path.join(backendRoot, ".venv", "bin", "python"),
  ];
  for (const c of candidates) {
    if (fs.existsSync(c)) return c;
  }
  return process.platform === "win32" ? "python" : "python3";
}

const openapiPath = path.join(here, "openapi.json");
const outPath = path.join(frontendRoot, "src", "lib", "api", "types.gen.ts");

execFileSync(findPython(), ["scripts/export_openapi.py", openapiPath], {
  cwd: backendRoot,
  stdio: "inherit",
});

const cli = path.join(frontendRoot, "node_modules", "openapi-typescript", "bin", "cli.js");
execFileSync(process.execPath, [cli, openapiPath, "-o", outPath], {
  cwd: frontendRoot,
  stdio: "inherit",
});

fs.rmSync(openapiPath, { force: true });
console.log(`wrote ${path.relative(frontendRoot, outPath)}`);
