"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { getPareto } from "@/lib/api/client";
import { CHART_THEME } from "@/lib/chartTheme";
import { fmtDurationS, fmtNumber } from "@/lib/format";
import { paretoKeyLabel } from "@/lib/labels";
import { paletteColor } from "@/lib/paretoPalette";

type By = "cause" | "station" | "fault_code";
type Metric = "duration" | "count";

const BY_OPTIONS: { value: By; label: string }[] = [
  { value: "cause", label: "Par cause" },
  { value: "station", label: "Par poste" },
  { value: "fault_code", label: "Par code défaut" },
];

/**
 * Every bar is stacked in two segments — "qualifié" (solid, one color per row) and "non
 * qualifié" (hatched). For `by=cause` this makes the dedicated "Non qualifié" row render as a
 * single fully-hatched bar automatically (its qualified share is always 0), so the same
 * rendering serves both the dedicated bar and the per-bucket hatched share without special-casing.
 */
export function ParetoChart({ from, to }: { from: string; to: string }) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const by = (searchParams.get("pareto_by") as By | null) ?? "cause";
  const metric = (searchParams.get("pareto_metric") as Metric | null) ?? "duration";

  function setParam(key: string, value: string): void {
    const sp = new URLSearchParams(searchParams);
    sp.set(key, value);
    router.push(`${pathname}?${sp.toString()}`);
  }

  const { data, isLoading, isError } = useQuery({
    queryKey: ["pareto", from, to, by, metric],
    queryFn: () => getPareto({ from, to, by, metric }),
  });

  const rows = (data?.buckets ?? []).map((b, i) => {
    const value = metric === "duration" ? b.duration_s : b.count;
    const unqualified = metric === "duration" ? b.unqualified_duration_s : b.unqualified_count;
    return {
      key: b.key,
      label: paretoKeyLabel(by, b.key),
      qualified: Math.max(0, value - unqualified),
      unqualified,
      color: paletteColor(i),
    };
  });

  const fmt = metric === "duration" ? fmtDurationS : (v: number | null) => fmtNumber(v);
  const height = Math.max(160, rows.length * 44 + 40);

  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900 p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold uppercase tracking-wide text-slate-400">
          Pareto des arrêts
        </h3>
        <div className="flex gap-2">
          <Select value={by} onChange={(v) => setParam("pareto_by", v)} options={BY_OPTIONS} />
          <Select
            value={metric}
            onChange={(v) => setParam("pareto_metric", v)}
            options={[
              { value: "duration", label: "Durée" },
              { value: "count", label: "Nombre" },
            ]}
          />
        </div>
      </div>

      {isLoading ? <p className="text-slate-400">Chargement…</p> : null}
      {isError ? <p className="text-red-400">Impossible de charger le Pareto.</p> : null}
      {data && rows.length === 0 ? (
        <p className="text-slate-400">Aucun arrêt sur cette période.</p>
      ) : null}

      {rows.length > 0 ? (
        <ResponsiveContainer width="100%" height={height}>
          <BarChart data={rows} layout="vertical" margin={{ left: 8, right: 16 }}>
            <defs>
              <pattern
                id="pareto-hatch"
                width="6"
                height="6"
                patternTransform="rotate(45)"
                patternUnits="userSpaceOnUse"
              >
                <rect width="6" height="6" fill={CHART_THEME.hatchBase} />
                <line x1="0" y1="0" x2="0" y2="6" stroke={CHART_THEME.hatchLine} strokeWidth="2" />
              </pattern>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke={CHART_THEME.grid} horizontal={false} />
            <XAxis
              type="number"
              stroke={CHART_THEME.axis}
              tickFormatter={(v: number) => fmt(v)}
              tick={{ fontSize: 11 }}
            />
            <YAxis
              type="category"
              dataKey="label"
              stroke={CHART_THEME.axis}
              width={180}
              tick={{ fontSize: 12 }}
            />
            <Tooltip
              contentStyle={{
                background: CHART_THEME.tooltipBg,
                border: `1px solid ${CHART_THEME.tooltipBorder}`,
              }}
              formatter={(value: number, name: string) => [
                fmt(value),
                name === "qualified" ? "Qualifié" : "Non qualifié",
              ]}
            />
            <Bar dataKey="qualified" stackId="a">
              {rows.map((r) => (
                <Cell key={r.key} fill={r.color} />
              ))}
            </Bar>
            <Bar dataKey="unqualified" stackId="a" fill="url(#pareto-hatch)" />
          </BarChart>
        </ResponsiveContainer>
      ) : null}

      {data && (data.micro_count > 0 || data.micro_duration_s > 0) ? (
        <p className="mt-2 text-xs text-slate-500">
          {data.micro_count} micro-arrêt{data.micro_count > 1 ? "s" : ""} non affiché
          {data.micro_count > 1 ? "s" : ""} ({fmtDurationS(data.micro_duration_s)} au total)
        </p>
      ) : null}
    </div>
  );
}

function Select<T extends string>({
  value,
  onChange,
  options,
}: {
  value: T;
  onChange: (v: T) => void;
  options: { value: T; label: string }[];
}) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value as T)}
      className="touch-target rounded border border-slate-700 bg-slate-800 px-2 py-2 text-sm text-slate-100"
    >
      {options.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  );
}
