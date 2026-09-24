/**
 * A neutral, categorical palette for the Pareto chart — deliberately NOT the andon palette
 * (lib/stateStyle.ts). Andon colors mean something specific about the cell's current state; a
 * Pareto bar is just "one of several causes/stations/fault codes" and must not borrow that
 * meaning by accident (a red Pareto bar next to a red "Défaut" andon tile would look related).
 */
export const PARETO_PALETTE = [
  "#38bdf8", // sky
  "#a78bfa", // violet
  "#2dd4bf", // teal
  "#fb923c", // orange (muted, distinct from andon's #f59e0b/#ea580c)
  "#60a5fa", // blue
  "#c084fc", // purple
  "#4ade80", // green (muted, distinct from andon's #16a34a)
  "#f472b6", // pink
  "#94a3b8", // slate
] as const;

export const UNQUALIFIED_COLOR = "#475569"; // slate-600: the hatched "non qualifié" fill base

export function paletteColor(index: number): string {
  return PARETO_PALETTE[index % PARETO_PALETTE.length] ?? UNQUALIFIED_COLOR;
}
