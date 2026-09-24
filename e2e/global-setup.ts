import { execFileSync } from "node:child_process";

/**
 * Brings up the full docker-compose stack (db -> seed -> simulator -> collector -> api ->
 * frontend) and waits until every healthchecked service reports healthy — `--wait` fails the
 * whole command if any of them doesn't, so a broken build or a crash-looping container fails
 * the test run here rather than timing out inside individual tests later.
 */
export default function globalSetup(): void {
  console.log("[global-setup] docker compose up --build --wait (this is the clean-machine test)");
  execFileSync(
    "docker",
    ["compose", "up", "--build", "-d", "--wait", "--wait-timeout", "180"],
    { stdio: "inherit", cwd: process.cwd() },
  );
}
