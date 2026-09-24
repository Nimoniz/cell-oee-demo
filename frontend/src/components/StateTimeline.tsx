"use client";

import { useQuery } from "@tanstack/react-query";

import { getTimeline } from "@/lib/api/client";
import { fmtDurationS } from "@/lib/format";
import { stateLabel, stateStyle } from "@/lib/stateStyle";
import { fmtTime } from "@/lib/time";

const REFRESH_MS = 5_000; // wall clock: a shift's history changes a few times a minute at most

/** Andon history of the current shift, from its start to now: one colored bar per state. */
export function StateTimeline() {
  const { data } = useQuery({
    queryKey: ["cell-timeline"],
    queryFn: getTimeline,
    refetchInterval: REFRESH_MS,
  });

  if (!data || !data.window_start || !data.now || data.segments.length === 0) {
    return (
      <div className="h-8 w-full animate-pulse rounded bg-slate-800" aria-hidden />
    );
  }

  const start = new Date(data.window_start).getTime();
  const end = new Date(data.now).getTime();
  const total = Math.max(1, end - start);

  return (
    <div>
      <div className="flex h-8 w-full overflow-hidden rounded border border-slate-800">
        {data.segments.map((seg) => {
          const segStart = new Date(seg.start).getTime();
          const segEnd = new Date(seg.end).getTime();
          const width = (Math.max(0, segEnd - segStart) / total) * 100;
          const style = stateStyle(seg.state, false);
          const duration = (segEnd - segStart) / 1000;
          return (
            <div
              key={seg.start}
              style={{ width: `${width}%`, backgroundColor: style.color }}
              title={`${stateLabel(seg.state)} — ${fmtTime(seg.start)} à ${fmtTime(seg.end)} (${fmtDurationS(duration)})`}
            />
          );
        })}
      </div>
      <div className="mt-1 flex justify-between text-xs text-slate-500">
        <span>Début du poste : {fmtTime(data.window_start)}</span>
        <span>Maintenant : {fmtTime(data.now)}</span>
      </div>
    </div>
  );
}
