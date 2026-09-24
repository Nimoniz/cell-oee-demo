/**
 * Bounds for the Analysis screen's quick-select buttons. Pure functions (no fetch, no clock
 * reads): they take "now" as an explicit ISO string — the plant time from the API, never the
 * browser's own clock — so they stay testable and never silently use the wrong time zone.
 */

const DAY_START_HOUR = 5; // production day starts at 05:00 plant time (CLAUDE.md)
export const SEVEN_DAYS_MS = 7 * 24 * 3600 * 1000;

/** The most recent 05:00 UTC at or before `nowIso` — the start of the current production day. */
export function productionDayStart(nowIso: string): string {
  const now = new Date(nowIso);
  const start = new Date(
    Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate(), DAY_START_HOUR),
  );
  if (start.getTime() > now.getTime()) {
    start.setUTCDate(start.getUTCDate() - 1);
  }
  return start.toISOString();
}

export function sevenDaysBefore(nowIso: string): string {
  return new Date(new Date(nowIso).getTime() - SEVEN_DAYS_MS).toISOString();
}
