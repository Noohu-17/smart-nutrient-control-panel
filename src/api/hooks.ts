// src/api/hooks.ts · TanStack Query hooks. Screens call these, never fetch.
import { useQueries, useQuery } from "@tanstack/react-query";
import { getJson } from "./client";
import type { Farm, ZoneTelemetry } from "./types";

const ONE_MINUTE = 60_000;

/** GET /api/health · is the back end reachable? Drives the Live chip in the top bar. */
export function useHealth() {
  return useQuery({
    queryKey: ["health"],
    queryFn: () => getJson<{ status: string; database: string; broker: string }>("/health"),
    refetchInterval: 30_000,
    retry: false,
  });
}

/** GET /api/farm · zones, benches, equipment. Changes rarely, so keep it for 5 minutes. */
export function useFarm() {
  return useQuery({
    queryKey: ["farm"],
    queryFn: () => getJson<Farm>("/farm"),
    staleTime: 5 * ONE_MINUTE,
  });
}

/** One zone's latest telemetry. Readings arrive about once a minute. */
function zoneQueryOptions(zoneId: string) {
  return {
    queryKey: ["zone", zoneId] as const,
    queryFn: () => getJson<ZoneTelemetry>(`/zones/${zoneId}`),
    refetchInterval: ONE_MINUTE,
  };
}

export function useZone(zoneId: string | undefined) {
  return useQuery({ ...zoneQueryOptions(zoneId ?? ""), enabled: Boolean(zoneId) });
}

/** Telemetry for several zones at once (the Overview). Same cache keys as useZone. */
export function useZones(zoneIds: string[]) {
  return useQueries({ queries: zoneIds.map(zoneQueryOptions) });
}
