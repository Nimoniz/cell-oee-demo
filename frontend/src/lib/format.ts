/**
 * Formatting for numbers, percentages and durations. `null` always renders as "—", never as 0
 * or 0 % — the API returns `null` precisely to distinguish "undefined" from "genuinely zero".
 */

function pad(n: number): string {
  return n.toString().padStart(2, "0");
}

const NUMBER = new Intl.NumberFormat("fr-FR", { maximumFractionDigits: 0 });
const KA = new Intl.NumberFormat("fr-FR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export function fmtPct(value: number | null, digits = 1): string {
  if (value === null) return "—";
  return `${(value * 100).toFixed(digits).replace(".", ",")} %`;
}

export function fmtNumber(value: number | null): string {
  if (value === null) return "—";
  return NUMBER.format(value);
}

export function fmtKa(value: number | null): string {
  if (value === null) return "—";
  return `${KA.format(value)} kA`;
}

/** Seconds -> a short French duration: "1 h 23 min", "5 min 30 s", "42 s". */
export function fmtDurationS(value: number | null): string {
  if (value === null) return "—";
  const total = Math.max(0, Math.round(value));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h > 0) return `${h} h ${pad(m)} min`;
  if (m > 0) return `${m} min ${pad(s)} s`;
  return `${s} s`;
}

/** "MM:SS" (or "H:MM:SS" past an hour): a compact, monospace-friendly duration for live tiles. */
export function fmtClock(value: number | null): string {
  if (value === null) return "—";
  const total = Math.max(0, Math.round(value));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${pad(m)}:${pad(s)}`;
}
