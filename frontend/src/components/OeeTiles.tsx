import { fmtPct } from "@/lib/format";
import type { OeeOut } from "@/lib/api/types";

/** A x P x Q = TRS, exactly as NF E60-182 defines it (see CLAUDE.md). Null renders as "—". */
export function OeeTiles({ oee, live }: { oee: OeeOut | null; live?: boolean }) {
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
      <OeeTile label="Disponibilité" value={oee?.availability ?? null} />
      <OeeTile label="Performance" value={oee?.performance ?? null} />
      <OeeTile label="Qualité" value={oee?.quality ?? null} />
      <OeeTile label={live ? "TRS (poste en cours)" : "TRS"} value={oee?.oee ?? null} primary />
    </div>
  );
}

function OeeTile({
  label,
  value,
  primary,
}: {
  label: string;
  value: number | null;
  primary?: boolean;
}) {
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900 px-4 py-3">
      <div className="text-xs uppercase tracking-wide text-slate-400">{label}</div>
      <div
        className={`tabular mt-1 font-semibold ${primary ? "text-4xl text-emerald-400" : "text-2xl"}`}
      >
        {fmtPct(value)}
      </div>
    </div>
  );
}
