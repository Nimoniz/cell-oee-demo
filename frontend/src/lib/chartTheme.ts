/**
 * Chart chrome (grid lines, axes, tooltip background, the Pareto hatch pattern): visual styling
 * that carries no andon meaning. Kept separate from lib/stateStyle.ts's `ANDON_COLORS` — and
 * from lib/paretoPalette.ts's categorical colors — so neither gets diluted with unrelated values.
 */
export const CHART_THEME = {
  grid: "#1e293b",
  axis: "#64748b",
  tooltipBg: "#0f172a",
  tooltipBorder: "#1e293b",
  hatchBase: "#475569",
  hatchLine: "#94a3b8",
  toleranceBand: "#334155",
  trs: "#34d399", // matches the emerald accent the live screen uses for its TRS tile
} as const;
