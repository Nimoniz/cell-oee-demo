/**
 * French UI labels for domain codes the API returns as plain integers/strings (CLAUDE.md:
 * code stays English, UI labels are French). One place, so the operator screen, the Pareto and
 * the weld markers never disagree on wording.
 */
import { stateLabel } from "./stateStyle";

export const FAULT_LABELS: Record<number, string> = {
  101: "Pièce absente (capteur de présence)",
  102: "Bridage non en position",
  103: "Barrière immatérielle coupée",
  201: "Courant de soudure hors tolérance",
  202: "Défaut robot",
  203: "Défaut débit d'eau de refroidissement pince",
  204: "Défaut rodeuse",
  301: "Clip absent / vissage non conforme",
  302: "Défaut système vision",
  303: "Bourrage évacuation",
  900: "Arrêt d'urgence",
};

export function faultLabel(code: number): string {
  return FAULT_LABELS[code] ?? `Défaut ${code}`;
}

export const STATION_LABELS: Record<string, string> = {
  OP10: "OP10 — Assemblage avant soudure",
  OP20: "OP20 — Soudure robotisée",
  OP30: "OP30 — Assemblage après soudure",
  CELL: "Cellule",
};

export function stationLabel(station: string): string {
  return STATION_LABELS[station] ?? station;
}

/** Short form ("OP20"), for chart axis ticks where the long descriptive label doesn't fit. */
export const STATION_SHORT: Record<string, string> = {
  OP10: "OP10",
  OP20: "OP20",
  OP30: "OP30",
  CELL: "Cellule",
};

export function stationShortLabel(station: string): string {
  return STATION_SHORT[station] ?? station;
}

/** The 8 operator qualification causes, in the order the touch grid presents them. */
export const CAUSE_LABELS: Record<string, string> = {
  mechanical_breakdown: "Panne mécanique",
  electrical_breakdown: "Panne électrique",
  setup_adjustment: "Réglage / mise au point",
  tool_change: "Changement d'outil",
  missing_parts: "Manque de pièces",
  downstream_saturation: "Saturation aval",
  quality_issue: "Problème qualité",
  other: "Autre",
};

export const CAUSES = Object.keys(CAUSE_LABELS);

export function causeLabel(cause: string | null): string {
  if (cause === null) return "Non qualifié";
  return CAUSE_LABELS[cause] ?? cause;
}

/**
 * A Pareto bucket's key, as the API sends it, turned into a French label:
 * - `by=cause`: a cause slug, or the literal `"unqualified"` for the dedicated bar;
 * - `by=station`: a station code (OP10/OP20/OP30/CELL);
 * - `by=fault_code`: a numeric fault code as text, or `"state_<n>"` for a loss category that
 *   carries no fault code (SETUP/STARVED/BLOCKED/MANUAL).
 */
export function paretoKeyLabel(by: "cause" | "station" | "fault_code", key: string): string {
  if (by === "cause") return key === "unqualified" ? "Non qualifié" : causeLabel(key);
  if (by === "station") return stationShortLabel(key);
  if (key.startsWith("state_")) return stateLabel(Number(key.slice("state_".length)));
  return faultLabel(Number(key));
}
