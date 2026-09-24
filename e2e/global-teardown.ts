import { execFileSync } from "node:child_process";

/**
 * Tears the stack down after the run — unless KEEP_STACK is set, which leaves the containers
 * and volumes in place so a failure can be inspected with `docker compose logs`.
 */
export default function globalTeardown(): void {
  if (process.env.KEEP_STACK) {
    console.log("[global-teardown] KEEP_STACK is set: leaving the stack running");
    return;
  }
  console.log("[global-teardown] docker compose down -v");
  execFileSync("docker", ["compose", "down", "-v"], { stdio: "inherit", cwd: process.cwd() });
}
