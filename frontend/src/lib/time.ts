/**
 * Simulated plant time, displayed exactly as received — UTC, never converted to the browser's
 * own time zone. Every timestamp formatted anywhere in the app must go through this module
 * (enforced by an ESLint rule banning local-time Date methods outside this file).
 */

function pad(n: number, width = 2): string {
  return n.toString().padStart(width, "0");
}

export function parseIso(value: string): Date {
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) {
    throw new Error(`invalid ISO datetime: ${value}`);
  }
  return d;
}

function asDate(value: string | Date): Date {
  return typeof value === "string" ? parseIso(value) : value;
}

/** "HH:mm:ss", UTC. */
export function fmtTime(value: string | Date): string {
  const d = asDate(value);
  return `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())}`;
}

/** "dd/MM/yyyy", UTC. */
export function fmtDate(value: string | Date): string {
  const d = asDate(value);
  return `${pad(d.getUTCDate())}/${pad(d.getUTCMonth() + 1)}/${d.getUTCFullYear()}`;
}

/** "dd/MM/yyyy HH:mm:ss", UTC. */
export function fmtDateTime(value: string | Date): string {
  return `${fmtDate(value)} ${fmtTime(value)}`;
}

/**
 * Turns a `<input type="datetime-local">` value ("2026-01-05T05:00" or "...:00") into an API
 * timestamp, treating it as plant (UTC) time — never the browser's local time zone.
 */
export function inputToApiIso(plantLocalValue: string): string {
  const withSeconds = /T\d{2}:\d{2}$/.test(plantLocalValue)
    ? `${plantLocalValue}:00`
    : plantLocalValue;
  return `${withSeconds}Z`;
}

/** The reverse of `inputToApiIso`: for pre-filling a `<input type="datetime-local">`. */
export function apiIsoToInput(value: string): string {
  return asDate(value).toISOString().slice(0, 16);
}
