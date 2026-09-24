"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { Suspense, useEffect } from "react";

import { OeeTrendChart } from "@/components/OeeTrendChart";
import { ParetoChart } from "@/components/ParetoChart";
import { PeriodPicker } from "@/components/PeriodPicker";
import { WeldChart } from "@/components/WeldChart";
import { getLive } from "@/lib/api/client";
import { sevenDaysBefore } from "@/lib/periods";

function AnalysisContent() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const from = searchParams.get("from");
  const to = searchParams.get("to");

  // Only needed to anchor the default 7-day window and the quick-select buttons — the charts
  // themselves are driven entirely by `from`/`to` in the URL, not by this "now".
  const { data: live } = useQuery({ queryKey: ["live-now"], queryFn: getLive, staleTime: 60_000 });

  useEffect(() => {
    if (!from && !to && live?.now) {
      const sp = new URLSearchParams(searchParams);
      sp.set("from", sevenDaysBefore(live.now));
      sp.set("to", live.now);
      router.replace(`${pathname}?${sp.toString()}`);
    }
  }, [from, to, live?.now, pathname, router, searchParams]);

  if (!from || !to) {
    return <div className="p-6 text-slate-400">Chargement…</div>;
  }

  return (
    <div className="mx-auto max-w-6xl space-y-6 px-4 py-6">
      <h1 className="text-xl font-semibold">Analyse</h1>
      <PeriodPicker from={from} to={to} nowIso={live?.now ?? null} />
      <ParetoChart from={from} to={to} />
      <OeeTrendChart from={from} to={to} />
      <WeldChart from={from} to={to} />
    </div>
  );
}

export default function AnalysisPage() {
  return (
    <Suspense fallback={<div className="p-6 text-slate-400">Chargement…</div>}>
      <AnalysisContent />
    </Suspense>
  );
}
