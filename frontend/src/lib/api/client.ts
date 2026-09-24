/**
 * A thin, typed fetch wrapper against the FastAPI backend. `apiUrl` is read at call time from
 * the runtime config (never baked in at build), so one built image works against any backend.
 */
import { getRuntimeConfig } from "../runtimeConfig";
import type {
  LiveOut,
  OeeOut,
  ParetoOut,
  QualifyIn,
  StopOut,
  TimelineOut,
  WeldOut,
} from "./types";

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const { apiUrl } = getRuntimeConfig();
  const res = await fetch(`${apiUrl}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = (await res.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
      else if (body.detail) detail = JSON.stringify(body.detail);
    } catch {
      // body was not JSON: keep statusText
    }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

function qs(params: Record<string, string | number | boolean | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined) search.set(key, String(value));
  }
  const s = search.toString();
  return s ? `?${s}` : "";
}

export function getLive(): Promise<LiveOut> {
  return request<LiveOut>("/api/cell/live");
}

export function getTimeline(): Promise<TimelineOut> {
  return request<TimelineOut>("/api/cell/timeline");
}

export function getOee(params: {
  from: string;
  to: string;
  group_by?: "shift" | "day";
}): Promise<OeeOut[]> {
  return request<OeeOut[]>(`/api/oee${qs(params)}`);
}

export function getStops(params: {
  unqualified?: boolean;
  from?: string;
  to?: string;
}): Promise<StopOut[]> {
  return request<StopOut[]>(`/api/stops${qs(params)}`);
}

export function qualifyStop(id: number, body: QualifyIn): Promise<StopOut> {
  return request<StopOut>(`/api/stops/${id}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export function getPareto(params: {
  from: string;
  to: string;
  by: "cause" | "station" | "fault_code";
  metric?: "duration" | "count";
}): Promise<ParetoOut> {
  return request<ParetoOut>(`/api/stops/pareto${qs(params)}`);
}

export function getWeld(params: { from: string; to: string }): Promise<WeldOut> {
  return request<WeldOut>(`/api/weld${qs(params)}`);
}
