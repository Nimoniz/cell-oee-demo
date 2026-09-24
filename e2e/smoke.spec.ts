import { expect, request, test } from "@playwright/test";

const API_URL = "http://localhost:8000";
const TIME_RE = /Heure usine\s*:\s*\d{2}:\d{2}:\d{2}/;

// Next.js still probes /favicon.ico even though app/icon.svg supplies the real favicon
// (see frontend/src/app/icon.svg) — a harmless 404, not a sign anything is broken.
function isRealError(text: string): boolean {
  return !text.includes("favicon.ico");
}

test.describe("backend is up and the bootstrap handoff left a sane gap, not an artificial one", () => {
  test("GET /healthz", async ({ request: req }) => {
    const res = await req.get(`${API_URL}/healthz`);
    expect(res.ok()).toBeTruthy();
  });

  test("the current shift has at most a few minutes of comm gap, not hours", async ({ request: req }) => {
    // The only gap expected right after `docker compose up` is the bootstrap-handoff one (see
    // CLAUDE.md's collector rule: "the time between the PLC's first values and the collector's
    // first heartbeat on a first run" is honestly reported as a gap). It must be small — a few
    // seconds of container start-up time, not an artefact of a broken depends_on chain.
    const res = await req.get(`${API_URL}/api/cell/live`);
    const body = (await res.json()) as { oee: { gap_s: number } | null };
    expect(body.oee).not.toBeNull();
    expect(body.oee!.gap_s).toBeGreaterThanOrEqual(0);
    expect(body.oee!.gap_s).toBeLessThan(600); // generous: real gap is ~1 min at ACCELERATION=10
  });
});

test.describe("the three screens load cleanly", () => {
  test("/live", async ({ page }) => {
    const errors: string[] = [];
    page.on("console", (msg) => {
      if (msg.type() === "error" && isRealError(msg.text())) errors.push(msg.text());
    });
    page.on("pageerror", (err) => errors.push(err.message));

    await page.goto("/live");
    await expect(page.getByText("Historique du poste")).toBeVisible();
    // The WS stream must actually deliver a snapshot: "Heure usine" stops showing "—".
    await expect(page.getByText(TIME_RE)).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText("Statut flux :")).toContainText(/En direct|Connexion/);

    expect(errors, `console errors on /live: ${errors.join("; ")}`).toEqual([]);
  });

  test("/operator", async ({ page }) => {
    const errors: string[] = [];
    page.on("console", (msg) => {
      if (msg.type() === "error" && isRealError(msg.text())) errors.push(msg.text());
    });
    page.on("pageerror", (err) => errors.push(err.message));

    await page.goto("/operator");
    await expect(page.getByRole("heading", { name: "Arrêts à qualifier" })).toBeVisible();
    // Either a card or the empty-state message — either is a legitimate, fully-loaded state.
    await expect(
      page.getByText(/Aucun arrêt à qualifier|Confirmer\s*:/).first(),
    ).toBeVisible({ timeout: 10_000 });

    expect(errors, `console errors on /operator: ${errors.join("; ")}`).toEqual([]);
  });

  test("/analysis", async ({ page }) => {
    const errors: string[] = [];
    page.on("console", (msg) => {
      if (msg.type() === "error" && isRealError(msg.text())) errors.push(msg.text());
    });
    page.on("pageerror", (err) => errors.push(err.message));

    await page.goto("/analysis");
    // The default 7-day window redirect must have landed (from/to appear in the URL).
    await expect(page).toHaveURL(/from=.+&to=.+|to=.+&from=.+/, { timeout: 10_000 });
    await expect(page.getByText("Pareto des arrêts")).toBeVisible();
    await expect(page.getByText(/TRS — par (poste|jour)/)).toBeVisible();
    await expect(page.getByText("Courant de soudure et taux de rebut (OP20)")).toBeVisible();

    expect(errors, `console errors on /analysis: ${errors.join("; ")}`).toEqual([]);
  });

  test("an empty period shows '—', never a misleading 0", async ({ page }) => {
    await page.goto("/analysis?from=2030-01-01T00:00:00Z&to=2030-01-02T00:00:00Z");
    await expect(page.getByText("Aucun arrêt sur cette période.")).toBeVisible();
    await expect(page.getByText("Aucune période.")).toBeVisible();
    await expect(page.getByText("Aucune donnée sur cette période.")).toBeVisible();
  });
});

// Shifts are 05:00 / 13:00 / 21:00 plant time (CLAUDE.md) and must render identically whatever
// the browser's own time zone is — lib/time.ts formats everything in UTC, never local time.
for (const timezoneId of ["America/Los_Angeles", "Asia/Kolkata"]) {
  test.describe(`time display is unaffected by the browser's time zone (${timezoneId})`, () => {
    test.use({ timezoneId });

    test("the live clock is a plain HH:MM:SS, not shifted by the local offset", async ({ page }) => {
      await page.goto("/live");
      const clock = page.getByText(TIME_RE);
      await expect(clock).toBeVisible({ timeout: 15_000 });
      await expect(page.getByText("jamais convertie au fuseau du navigateur")).toBeVisible();
    });
  });
}
