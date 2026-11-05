// src/api/hooks.ts · TanStack Query hooks. Screens call these, never fetch.
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { getJson, postJson } from "./client";
import { isFinalCommand, isMixing } from "../config/dosing";
import type {
  ClimateTelemetry,
  Command,
  DoseConfirmed,
  DoseRequest,
  DoseRequestBody,
  Farm,
  PowerState,
  ZoneStatus,
} from "./types";

const ONE_MINUTE = 60_000;
const COMMAND_POLL_MS = 1000;
const MIXING_POLL_MS = 2000;
// How long to keep waiting for `after` once the lines have finished. The mix wait itself is the
// farm's (limits.json mixWaitS); this only stops the app asking for ever when a stop or a reboot
// cut the wait short and `after` will never come (§6.6).
const MIXING_FOLLOW_MS = 15 * ONE_MINUTE;
const finishedAt = new Map<string, number>();

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
    queryFn: () => getJson<ZoneStatus>(`/zones/${zoneId}`),
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

/** GET /api/climate · air inside and outside, electrical room, climate equipment. Every minute. */
export function useClimate() {
  return useQuery({
    queryKey: ["climate"],
    queryFn: () => getJson<ClimateTelemetry>("/climate"),
    refetchInterval: ONE_MINUTE,
  });
}

/** GET /api/power · mains, generator and UPS. Sent every minute and on every change. */
export function usePower() {
  return useQuery({
    queryKey: ["power"],
    queryFn: () => getJson<PowerState>("/power"),
    refetchInterval: ONE_MINUTE,
  });
}

/** Run the safety checks for a dose (§11.3). No side effects: nothing is sent to the farm. */
export function useDoseReview() {
  return useMutation({
    mutationFn: (body: DoseRequestBody) => postJson<DoseRequest>("/dose-requests", body),
  });
}

/** Create and dispatch the command for a reviewed dose. */
export function useConfirmDose() {
  return useMutation({
    mutationFn: ({ id, key }: { id: string; key: string }) =>
      postJson<DoseConfirmed>(`/dose-requests/${id}/confirm`, undefined, { "Idempotency-Key": key }),
  });
}

/** Follow a command until the farm reports a final state, then until `after` arrives (mix wait).
    The WebSocket replaces the polling later (§12). */
export function useCommand(cmdId: string) {
  return useQuery({
    queryKey: ["command", cmdId],
    queryFn: () => getJson<Command>(`/commands/${cmdId}`),
    refetchInterval: (query) => {
      const command = query.state.data;
      if (!command || !isFinalCommand(command.state)) return COMMAND_POLL_MS;
      if (!isMixing(command)) return false;
      if (!finishedAt.has(cmdId)) finishedAt.set(cmdId, Date.now());
      return Date.now() - finishedAt.get(cmdId)! < MIXING_FOLLOW_MS ? MIXING_POLL_MS : false;
    },
  });
}

/** Stop dosing and refill in a zone (§7.4). Never checked, never confirmed, never retried. */
export function useStopZone() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (zone: string) =>
      postJson<{ state: string }>(
        "/stop",
        { scope: "ZONE", zone },
        { "Idempotency-Key": crypto.randomUUID() },
      ),
    onSuccess: (_answer, zone) => {
      void queryClient.invalidateQueries({ queryKey: ["command"] });
      void queryClient.invalidateQueries({ queryKey: ["zone", zone] });
    },
  });
}

/** Resume a stopped zone once a person has checked it (§7.8). Nothing restarts by itself. */
export function useResumeZone() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (zone: string) => postJson<{ cmdId: string; state: string }>("/resume", { zone }),
    onSuccess: (_answer, zone) => queryClient.invalidateQueries({ queryKey: ["zone", zone] }),
  });
}
