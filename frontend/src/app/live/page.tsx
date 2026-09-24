"use client";

import { OeeTiles } from "@/components/OeeTiles";
import { StateBadge } from "@/components/StateBadge";
import { StateTimeline } from "@/components/StateTimeline";
import { Tile } from "@/components/Tile";
import { fmtDurationS, fmtKa, fmtNumber } from "@/lib/format";
import { faultLabel } from "@/lib/labels";
import { useLive } from "@/lib/live/useLive";
import { ANDON_COLORS } from "@/lib/stateStyle";
import { fmtTime } from "@/lib/time";

const STATUS_LABEL: Record<string, string> = {
  connecting: "Connexion…",
  live: "En direct",
  stalled: "Flux interrompu",
  disconnected: "Déconnecté",
};

export default function LivePage() {
  const { data, status } = useLive();
  // Scoped to the current shift (same source as the TRS panel below), not the PLC's raw
  // cumulative counters, so "pièces bonnes" and the quality ratio never disagree.
  const good = data?.oee?.good ?? null;
  const scrap = data?.oee?.scrap ?? null;
  const total = good !== null && scrap !== null ? good + scrap : null;

  return (
    <div className="mx-auto max-w-5xl space-y-6 px-4 py-6">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <StateBadge state={data?.state ?? null} commLost={data?.comm_lost ?? false} size="lg" />
        <div className="text-right text-sm text-slate-400">
          <div>
            Statut flux :{" "}
            <span className={status === "live" ? "text-emerald-400" : "text-amber-400"}>
              {STATUS_LABEL[status]}
            </span>
          </div>
          <div className="tabular">Heure usine : {data?.now ? fmtTime(data.now) : "—"}</div>
        </div>
      </div>

      <section>
        <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-slate-400">
          Historique du poste
        </h2>
        <StateTimeline />
      </section>

      {data?.comm_lost ? (
        <div className="rounded-lg border border-slate-700 bg-slate-800/60 px-4 py-3 text-slate-300">
          Communication perdue
          {data.comm_lost_since ? ` depuis ${fmtTime(data.comm_lost_since)}` : ""}
        </div>
      ) : data?.state === 3 ? (
        <div className="rounded-lg border border-red-900 bg-red-950/60 px-4 py-3 text-red-200">
          Défaut {data.fault_code} — {faultLabel(data.fault_code ?? 0)}
        </div>
      ) : null}

      <section>
        <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-slate-400">
          Production — poste en cours
        </h2>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <Tile label="Pièces bonnes" value={fmtNumber(good)} accent={ANDON_COLORS.producing} />
          <Tile label="Pièces rebutées" value={fmtNumber(scrap)} accent={ANDON_COLORS.fault} />
          <Tile label="Total produit" value={fmtNumber(total)} />
          <Tile
            label="Temps de cycle"
            value={
              data?.last_cycle_s !== null && data?.last_cycle_s !== undefined
                ? `${data.last_cycle_s.toFixed(1)} s`
                : "—"
            }
          />
        </div>
      </section>

      <section>
        <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-slate-400">
          TRS — poste en cours
        </h2>
        <OeeTiles oee={data?.oee ?? null} live />
      </section>

      <section>
        <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-slate-400">
          Soudure (OP20)
        </h2>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <Tile label="Courant du dernier point" value={fmtKa(data?.weld_current_ka ?? null)} />
          <Tile
            label="Points depuis changement de capuchons"
            value={fmtNumber(data?.weld_points_since_cap_change ?? null)}
          />
        </div>
      </section>

      {data?.open_stop ? (
        <section>
          <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-slate-400">
            Arrêt en cours
          </h2>
          <div className="rounded-lg border border-slate-800 bg-slate-900 px-4 py-3">
            <div className="tabular text-2xl font-semibold">
              {fmtDurationS(data.open_stop.duration_s)}
            </div>
            <div className="text-sm text-slate-400">
              Depuis {fmtTime(data.open_stop.start_ts)} — {data.open_stop.origin_station}
            </div>
          </div>
        </section>
      ) : null}
    </div>
  );
}
