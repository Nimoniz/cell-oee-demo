"use client";

import { useQuery } from "@tanstack/react-query";
import {
  Area,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ReferenceArea,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { getWeld } from "@/lib/api/client";
import { CHART_THEME } from "@/lib/chartTheme";
import { fmtKa, fmtPct } from "@/lib/format";
import { paletteColor } from "@/lib/paretoPalette";
import { fmtTime } from "@/lib/time";

const CURRENT_COLOR = paletteColor(0);
const SCRAP_COLOR = paletteColor(3);

/**
 * The electrode-wear curve: weld current (with its min/max band and tolerance) on the left
 * axis, scrap rate on the right, tip-dressing and cap-change markers overlaid. A bucket with no
 * weld point leaves a gap in the current line — never a misleading drop to 0.
 */
export function WeldChart({ from, to }: { from: string; to: string }) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["weld", from, to],
    queryFn: () => getWeld({ from, to }),
  });

  const rows = (data?.buckets ?? []).map((b) => ({
    x: b.start,
    avg: b.avg_ka,
    range: b.min_ka !== null && b.max_ka !== null ? [b.min_ka, b.max_ka] : null,
    scrapRate: b.scrap_rate,
  }));
  // Buckets span the whole requested range regardless of data (like an empty grid of time
  // slots), so `rows.length` alone can't tell an empty period from a full one: check whether
  // any bucket actually has a value.
  const hasData = rows.some((r) => r.avg !== null || r.scrapRate !== null);

  const toleranceLow = data ? data.nominal_ka * (1 - data.tolerance_pct / 100) : 0;
  const toleranceHigh = data ? data.nominal_ka * (1 + data.tolerance_pct / 100) : 0;

  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900 p-4">
      <h3 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-400">
        Courant de soudure et taux de rebut (OP20)
      </h3>

      {isLoading ? <p className="text-slate-400">Chargement…</p> : null}
      {isError ? <p className="text-red-400">Impossible de charger la courbe de soudure.</p> : null}
      {data && !hasData ? <p className="text-slate-400">Aucune donnée sur cette période.</p> : null}

      {hasData ? (
        <ResponsiveContainer width="100%" height={360}>
          <ComposedChart data={rows} margin={{ left: 8, right: 16, top: 8 }}>
            <CartesianGrid strokeDasharray="3 3" stroke={CHART_THEME.grid} />
            <XAxis
              dataKey="x"
              stroke={CHART_THEME.axis}
              tickFormatter={(v: string) => fmtTime(v)}
              tick={{ fontSize: 11 }}
            />
            <YAxis
              yAxisId="current"
              domain={["auto", "auto"]}
              stroke={CHART_THEME.axis}
              tickFormatter={(v: number) => fmtKa(v)}
              tick={{ fontSize: 11 }}
              width={70}
            />
            <YAxis
              yAxisId="scrap"
              orientation="right"
              domain={[0, "auto"]}
              stroke={CHART_THEME.axis}
              tickFormatter={(v: number) => fmtPct(v, 0)}
              tick={{ fontSize: 11 }}
            />
            <ReferenceArea
              yAxisId="current"
              y1={toleranceLow}
              y2={toleranceHigh}
              fill={CHART_THEME.toleranceBand}
              fillOpacity={0.35}
              ifOverflow="extendDomain"
            />
            <Tooltip
              contentStyle={{
                background: CHART_THEME.tooltipBg,
                border: `1px solid ${CHART_THEME.tooltipBorder}`,
              }}
              formatter={(value, name) => {
                if (name === "range" || value === null || typeof value !== "number") {
                  return [null, null];
                }
                if (name === "scrapRate") return [fmtPct(value), "Taux de rebut"];
                return [fmtKa(value), "Courant moyen"];
              }}
              labelFormatter={(v: string) => fmtTime(v)}
            />
            <Legend wrapperStyle={{ fontSize: 12 }} />
            <Area
              yAxisId="current"
              dataKey="range"
              name="Min–max"
              stroke="none"
              fill={CURRENT_COLOR}
              fillOpacity={0.15}
              connectNulls={false}
              isAnimationActive={false}
            />
            <Line
              yAxisId="current"
              dataKey="avg"
              name="Courant moyen"
              stroke={CURRENT_COLOR}
              strokeWidth={2}
              dot={false}
              connectNulls={false}
            />
            <Line
              yAxisId="scrap"
              dataKey="scrapRate"
              name="Taux de rebut"
              stroke={SCRAP_COLOR}
              strokeWidth={2}
              dot={false}
              connectNulls={false}
            />
            {data?.markers.map((m) => (
              <ReferenceLine
                key={m.ts}
                yAxisId="current"
                x={m.ts}
                stroke={CHART_THEME.axis}
                strokeDasharray={m.kind === "cap_change" ? undefined : "3 3"}
                strokeWidth={m.kind === "cap_change" ? 2 : 1}
              />
            ))}
          </ComposedChart>
        </ResponsiveContainer>
      ) : null}
      <p className="mt-2 text-xs text-slate-500">
        Bande grisée : tolérance ±{data?.tolerance_pct ?? "…"} % autour de {fmtKa(data?.nominal_ka ?? null)}.
        Traits verticaux : rodage (pointillé), changement de capuchons (plein).
      </p>
    </div>
  );
}
