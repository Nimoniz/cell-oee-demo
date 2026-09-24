"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";

import { ApiError, qualifyStop } from "@/lib/api/client";
import type { StopOut } from "@/lib/api/types";
import { fmtDurationS } from "@/lib/format";
import { CAUSE_LABELS, CAUSES, causeLabel, faultLabel, stationLabel } from "@/lib/labels";
import { stateLabel } from "@/lib/stateStyle";
import { fmtTime } from "@/lib/time";

/** One qualifiable stop, tablet-first: one tap confirms the suggestion, or picks another cause. */
export function StopCard({ stop }: { stop: StopOut }) {
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationFn: (cause: string) => qualifyStop(stop.id, { cause }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["stops"] });
    },
  });

  const title = stop.fault_code !== 0 ? faultLabel(stop.fault_code) : stateLabel(stop.state);
  const suggested = stop.suggested_category;

  return (
    <div
      className={`rounded-xl border bg-slate-900 p-4 ${
        stop.open ? "border-amber-600" : "border-slate-800"
      }`}
    >
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <div className="flex items-center gap-2">
            <span className="text-lg font-semibold">{title}</span>
            {stop.open ? (
              <span className="inline-flex items-center rounded-full bg-amber-600 px-2 py-0.5 text-xs font-bold tracking-wide text-white">
                EN COURS
              </span>
            ) : null}
          </div>
          <div className="text-sm text-slate-400">
            {stationLabel(stop.origin_station)} — depuis {fmtTime(stop.start_ts)}
          </div>
        </div>
        <div className="tabular text-2xl font-semibold text-amber-400">
          {fmtDurationS(stop.duration_s)}
        </div>
      </div>

      {mutation.isError ? (
        <p className="mt-2 text-sm text-red-400">
          {mutation.error instanceof ApiError ? mutation.error.message : "Échec de la validation"}
        </p>
      ) : null}

      <div className="mt-4">
        {suggested !== null ? (
          <button
            type="button"
            disabled={mutation.isPending}
            onClick={() => mutation.mutate(suggested)}
            className="touch-target w-full rounded-lg bg-emerald-600 px-4 py-3 text-lg font-semibold text-white hover:bg-emerald-500 disabled:opacity-50"
          >
            Confirmer : {causeLabel(suggested)}
          </button>
        ) : null}

        <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
          {CAUSES.filter((c) => c !== suggested).map((cause) => (
            <button
              key={cause}
              type="button"
              disabled={mutation.isPending}
              onClick={() => mutation.mutate(cause)}
              className="touch-target rounded-lg border border-slate-700 bg-slate-800 px-2 py-3 text-sm hover:bg-slate-700 disabled:opacity-50"
            >
              {CAUSE_LABELS[cause]}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
