/**
 * The andon convention: one color, one label, one icon per cell state, defined here ONCE.
 * Mirrored as design tokens in tailwind.config.ts (`andon.*`) for class-based styling; this
 * file is the source of truth for contexts that need the raw values (inline SVG, Recharts,
 * the WS message's raw state code). State is never shown by color alone: label + icon are
 * always rendered alongside it.
 */

/** The one place the andon hex values are written down. `tailwind.config.ts` imports this. */
export const ANDON_COLORS = {
  producing: "#16a34a",
  fault: "#dc2626",
  starved: "#f59e0b",
  blocked: "#ea580c",
  planned: "#2563eb",
  setup: "#7c3aed", // also used for MANUAL: one color, distinguished by icon and label
  lost: "#6b7280",
} as const;

export const CellState = {
  PRODUCING: 1,
  PLANNED_STOP: 2,
  FAULT: 3,
  SETUP: 4,
  STARVED: 5,
  BLOCKED: 6,
  MANUAL: 7,
} as const;

export type CellStateCode = (typeof CellState)[keyof typeof CellState];

export interface StateStyle {
  label: string;
  color: string;
  icon: string;
}

const STYLES: Record<CellStateCode, StateStyle> = {
  [CellState.PRODUCING]: { label: "Production", color: ANDON_COLORS.producing, icon: "●" },
  [CellState.PLANNED_STOP]: { label: "Arrêt planifié", color: ANDON_COLORS.planned, icon: "⏸" },
  [CellState.FAULT]: { label: "Défaut", color: ANDON_COLORS.fault, icon: "■" },
  [CellState.SETUP]: { label: "Réglage", color: ANDON_COLORS.setup, icon: "🔧" },
  [CellState.STARVED]: { label: "Attente amont", color: ANDON_COLORS.starved, icon: "▲" },
  [CellState.BLOCKED]: { label: "Attente aval", color: ANDON_COLORS.blocked, icon: "▼" },
  [CellState.MANUAL]: { label: "Manuel", color: ANDON_COLORS.setup, icon: "✋" },
};

export const COMM_LOST_STYLE: StateStyle = {
  label: "Communication perdue",
  color: ANDON_COLORS.lost,
  icon: "◌",
};

const UNKNOWN_STYLE: StateStyle = { label: "—", color: ANDON_COLORS.lost, icon: "?" };

function isCellStateCode(state: number): state is CellStateCode {
  return state in STYLES;
}

/** The style for a cell state; `commLost` overrides it (a lost connection hides the last state). */
export function stateStyle(state: number | null, commLost: boolean): StateStyle {
  if (commLost) return COMM_LOST_STYLE;
  if (state === null || !isCellStateCode(state)) return UNKNOWN_STYLE;
  return STYLES[state];
}

export function stateLabel(state: number): string {
  return isCellStateCode(state) ? STYLES[state].label : `État ${state}`;
}
