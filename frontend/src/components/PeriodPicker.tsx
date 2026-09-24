"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";

import { getTimeline } from "@/lib/api/client";
import { productionDayStart, sevenDaysBefore } from "@/lib/periods";
import { apiIsoToInput, inputToApiIso } from "@/lib/time";

export function PeriodPicker({
  from,
  to,
  nowIso,
}: {
  from: string;
  to: string;
  nowIso: string | null;
}) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const [draftFrom, setDraftFrom] = useState(() => apiIsoToInput(from));
  const [draftTo, setDraftTo] = useState(() => apiIsoToInput(to));

  function setPeriod(newFrom: string, newTo: string): void {
    const sp = new URLSearchParams(searchParams);
    sp.set("from", newFrom);
    sp.set("to", newTo);
    router.push(`${pathname}?${sp.toString()}`);
    setDraftFrom(apiIsoToInput(newFrom));
    setDraftTo(apiIsoToInput(newTo));
  }

  async function selectCurrentShift(): Promise<void> {
    const timeline = await getTimeline();
    if (timeline.window_start && timeline.now) setPeriod(timeline.window_start, timeline.now);
  }

  function selectProductionDay(): void {
    if (nowIso) setPeriod(productionDayStart(nowIso), nowIso);
  }

  function selectSevenDays(): void {
    if (nowIso) setPeriod(sevenDaysBefore(nowIso), nowIso);
  }

  function applyCustomRange(): void {
    setPeriod(inputToApiIso(draftFrom), inputToApiIso(draftTo));
  }

  return (
    <div className="flex flex-wrap items-end gap-3 rounded-lg border border-slate-800 bg-slate-900 p-3">
      <div className="flex gap-2">
        <QuickButton onClick={() => void selectCurrentShift()}>Poste en cours</QuickButton>
        <QuickButton onClick={selectProductionDay} disabled={!nowIso}>
          Jour de production
        </QuickButton>
        <QuickButton onClick={selectSevenDays} disabled={!nowIso}>
          7 jours
        </QuickButton>
      </div>
      <div className="flex flex-wrap items-end gap-2">
        <label className="text-xs text-slate-400">
          Du (heure usine)
          <input
            type="datetime-local"
            value={draftFrom}
            onChange={(e) => setDraftFrom(e.target.value)}
            className="mt-1 block rounded border border-slate-700 bg-slate-800 px-2 py-2 text-sm text-slate-100"
          />
        </label>
        <label className="text-xs text-slate-400">
          Au (heure usine)
          <input
            type="datetime-local"
            value={draftTo}
            onChange={(e) => setDraftTo(e.target.value)}
            className="mt-1 block rounded border border-slate-700 bg-slate-800 px-2 py-2 text-sm text-slate-100"
          />
        </label>
        <button
          type="button"
          onClick={applyCustomRange}
          className="touch-target rounded border border-slate-700 bg-slate-800 px-3 py-2 text-sm hover:bg-slate-700"
        >
          Appliquer
        </button>
      </div>
    </div>
  );
}

function QuickButton({
  onClick,
  disabled,
  children,
}: {
  onClick: () => void;
  disabled?: boolean;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="touch-target rounded border border-slate-700 bg-slate-800 px-3 py-2 text-sm hover:bg-slate-700 disabled:opacity-40"
    >
      {children}
    </button>
  );
}
