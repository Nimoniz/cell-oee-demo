import { defineConfig } from "@playwright/test";

// One suite, run against the real docker-compose stack (see e2e/global-setup.ts): this is the
// "does it actually work end to end, on a clean machine" check, not a substitute for the unit
// and integration tests each service already has. Deliberately serial (workers: 1) — the stack
// only exists once, and the tests read its live, ever-advancing state.
export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  globalSetup: "./e2e/global-setup.ts",
  globalTeardown: "./e2e/global-teardown.ts",
  use: {
    baseURL: "http://localhost:3000",
    trace: "retain-on-failure",
  },
});
