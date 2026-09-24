"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { getOee } from "@/lib/api/client";
import { CHART_THEME } from "@/lib/chartTheme";
import { fmtPct } from "@/lib/format";
import { paletteColor } from "@/lib/paretoPalette";

type GroupBy = "shift" | "day";

const SERIES = [
  { key: "availability", label: "Disponibilité", color: paletteColor(4), width: 1.5 },
  { key: "performance", label: "Performance", color: paletteColor(1), width: 1.5 },
  { key: "quality", label: "Qualité", color: paletteColor(2), width: 1.5 },
  { key: "oee", label: "TRS", color: CHART_THEME.trs, width: 3 },
] as const;

/** A/P/Q as thin lines, TRS bold — the same NF E60-182 breakdown as the live screen's tiles,
 * but across shifts or days so a trend is visible. `null` leaves a gap in the line, never a 0. */
export function OeeTrendChart({ from, to }: { from: string; to: string }) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const groupBy = (searchParams.get("oee_group") as GroupBy | null) ?? "shift";

  function setGroupBy(value: GroupBy): void {
    const sp = new URLSearchParams(searchParams);
    sp.set("oee_group", value);
    router.push(`${pathname}?${sp.toString()}`);
  }

  const { data, isLoading, isError } = useQuery({
    queryKey: ["oee-trend", from, to, groupBy],
    queryFn: () => getOee({ from, to, group_by: groupBy }),
  });

  const rows = data ?? [];

  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900 p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold uppercase tracking-wide text-slate-400">
          TRS — {groupBy === "shift" ? "par poste" : "par jour"}
        </h3>
        <div className="flex gap-2">
          <ToggleButton active={groupBy === "shift"} onClick={() => setGroupBy("shift")}>
            Par poste
          </ToggleButton>
          <ToggleButton active={groupBy === "day"} onClick={() => setGroupBy("day")}>
            Par jour
          </ToggleButton>
        </div>
      </div>

      {isLoading ? <p className="text-slate-400">Chargement…</p> : null}
      {isError ? <p className="text-red-400">Impossible de charger le TRS.</p> : null}
      {data && rows.length === 0 ? <p className="text-slate-400">Aucune période.</p> : null}

      {rows.length > 0 ? (
        <>
          <ResponsiveContainer width="100%" height={320}>
            <LineChart data={rows} margin={{ left: 8, right: 16 }}>
              <CartesianGrid strokeDasharray="3 3" stroke={CHART_THEME.grid} />
              <XAxis dataKey="label" stroke={CHART_THEME.axis} tick={{ fontSize: 11 }} />
              <YAxis
                domain={[0, 1]}
                stroke={CHART_THEME.axis}
                tickFormatter={(v: number) => fmtPct(v, 0)}
                tick={{ fontSize: 11 }}
              />
              <Tooltip
                contentStyle={{
                  background: CHART_THEME.tooltipBg,
                  border: `1px solid ${CHART_THEME.tooltipBorder}`,
                }}
                formatter={(value, name) => {
                  const numeric = typeof value === "number" ? value : null;
                  const s = SERIES.find((s) => s.label === name);
                  return [fmtPct(numeric), s?.label ?? String(name)];
                }}
              />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              {SERIES.map((s) => (
                <Line
                  key={s.key}
                  dataKey={s.key}
                  name={s.label}
                  stroke={s.color}
                  strokeWidth={s.width}
                  dot={s.key === "oee" ? { r: 3 } : false}
                  connectNulls={false}
                />
              ))}
            </LineChart>
          </ResponsiveContainer>

          <div className="mt-4 overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="text-slate-400">
                <tr>
                  <th className="py-1 pr-4">Période</th>
                  <th className="py-1 pr-4">Disponibilité</th>
                  <th className="py-1 pr-4">Performance</th>
                  <th className="py-1 pr-4">Qualité</th>
                  <th className="py-1 pr-4">TRS</th>
                </tr>
              </thead>
              <tbody className="tabular">
                {rows.map((r) => (
                  <tr key={r.label} className="border-t border-slate-800">
                    <td className="py-1 pr-4">{r.label}</td>
                    <td className="py-1 pr-4">{fmtPct(r.availability)}</td>
                    <td className="py-1 pr-4">{fmtPct(r.performance)}</td>
                    <td className="py-1 pr-4">{fmtPct(r.quality)}</td>
                    <td className="py-1 pr-4 font-semibold">{fmtPct(r.oee)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : null}
    </div>
  );
}

function ToggleButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`touch-target rounded border px-3 py-2 text-sm ${
        active
          ? "border-emerald-600 bg-emerald-600/20 text-emerald-300"
          : "border-slate-700 bg-slate-800 hover:bg-slate-700"
      }`}
    >
      {children}
    </button>
  );
}
