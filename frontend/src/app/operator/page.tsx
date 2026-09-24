"use client";

import { useQuery } from "@tanstack/react-query";

import { StopCard } from "@/components/StopCard";
import { getStops } from "@/lib/api/client";
import { useLive } from "@/lib/live/useLive";

const REFRESH_MS = 3_000; // wall clock: enough to keep an open stop's duration current
const BACKLOG_HOURS = 24; // simulated hours: caps the list at high ACCELERATION

export default function OperatorPage() {
  const { data: live } = useLive();
  const { data, isLoading, isError } = useQuery({
    queryKey: ["stops"],
    queryFn: () => getStops({ unqualified: true }),
    refetchInterval: REFRESH_MS,
  });

  const cutoff = live?.now
    ? new Date(new Date(live.now).getTime() - BACKLOG_HOURS * 3_600_000).toISOString()
    : null;
  const visible = cutoff ? (data?.filter((s) => s.start_ts >= cutoff) ?? []) : data;
  const hiddenCount = data && visible ? data.length - visible.length : 0;

  return (
    <div className="mx-auto max-w-3xl space-y-4 px-4 py-6">
      <h1 className="text-xl font-semibold">Arrêts à qualifier</h1>
      <p className="text-sm text-slate-400">
        Seuls les arrêts d&apos;au moins 120 s apparaissent ici, y compris l&apos;arrêt en cours
        dès qu&apos;il atteint ce seuil.
      </p>

      {isLoading ? <p className="text-slate-400">Chargement…</p> : null}
      {isError ? <p className="text-red-400">Impossible de charger les arrêts.</p> : null}
      {visible && visible.length === 0 ? (
        <p className="text-slate-400">Aucun arrêt à qualifier pour le moment.</p>
      ) : null}

      <div className="space-y-3">
        {visible?.map((stop) => <StopCard key={stop.id} stop={stop} />)}
      </div>

      {hiddenCount > 0 ? (
        <p className="text-center text-sm text-slate-500">
          + {hiddenCount} arrêt{hiddenCount > 1 ? "s" : ""} plus ancien
          {hiddenCount > 1 ? "s" : ""} (plus de {BACKLOG_HOURS} h), non affiché
          {hiddenCount > 1 ? "s" : ""}
        </p>
      ) : null}
    </div>
  );
}
