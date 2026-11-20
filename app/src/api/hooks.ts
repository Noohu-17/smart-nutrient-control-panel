// src/api/hooks.ts · TanStack Query hooks. Screens call these, never fetch.
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, deleteJson, getJson, postJson } from "./client";
import { isFinalCommand, isMixing } from "../config/dosing";
import type {
  ClimateStatus,
  Command,
  DoseConfirmed,
  DoseLimits,
  DoseRequest,
  DoseRequestBody,
  Farm,
  PowerStatus,
  ScenarioOptions,
  ServerAlert,
  SimFaultBody,
  SimHold,
  SimSpeed,
  SimState,
  ZoneStatus,
} from "./types";

const ONE_MINUTE = 60_000;
// A playground site refreshes every 5 s, so a change made on the Simulator screen shows on the
// dashboard within 10 s (one simulator reading plus one refresh). A real farm reports about once a
// minute, so it keeps the slower refresh.
const PLAYGROUND_POLL_MS = 5_000;
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

/** How often to ask again: every 5 s on a playground site, otherwise `slow`. */
function usePollMs(slow: number) {
  const farm = useFarm();
  return farm.data?.features.playground ? PLAYGROUND_POLL_MS : slow;
}

/** GET /api/alerts · the server's alerts, newest first (T1-24). As often as the zones, and on
    while the tab is in the background too, so a new alert can still notify. */
export function useAlerts() {
  const pollMs = usePollMs(ONE_MINUTE);
  return useQuery({
    queryKey: ["alerts"],
    queryFn: () => getJson<{ items: ServerAlert[] }>("/alerts").then((answer) => answer.items),
    refetchInterval: pollMs,
    refetchIntervalInBackground: true,
  });
}

/** POST /api/alerts/{id}/ack · record that someone has seen it; it stays until its condition ends. */
export function useAckAlert() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (alertId: string) => postJson<ServerAlert>(`/alerts/${encodeURIComponent(alertId)}/ack`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["alerts"] }),
  });
}

/** One zone's latest telemetry. A real farm's readings arrive about once a minute. */
function zoneQueryOptions(zoneId: string, pollMs: number) {
  return {
    queryKey: ["zone", zoneId] as const,
    queryFn: () => getJson<ZoneStatus>(`/zones/${zoneId}`),
    refetchInterval: pollMs,
  };
}

export function useZone(zoneId: string | undefined) {
  const pollMs = usePollMs(ONE_MINUTE);
  return useQuery({ ...zoneQueryOptions(zoneId ?? "", pollMs), enabled: Boolean(zoneId) });
}

/** Telemetry for several zones at once (the Overview). Same cache keys as useZone. */
export function useZones(zoneIds: string[]) {
  const pollMs = usePollMs(ONE_MINUTE);
  return useQueries({ queries: zoneIds.map((zoneId) => zoneQueryOptions(zoneId, pollMs)) });
}

/** GET /api/climate · air inside and outside, electrical room, climate equipment. Every minute. */
export function useClimate() {
  const pollMs = usePollMs(ONE_MINUTE);
  return useQuery({
    queryKey: ["climate"],
    queryFn: () => getJson<ClimateStatus>("/climate"),
    refetchInterval: pollMs,
  });
}

/** GET /api/power · mains, generator and UPS. Sent every minute and on every change. */
export function usePower() {
  const pollMs = usePollMs(ONE_MINUTE);
  return useQuery({
    queryKey: ["power"],
    queryFn: () => getJson<PowerStatus>("/power"),
    refetchInterval: pollMs,
  });
}

/** GET /api/zones/{zoneId}/dose-limits · each line's limits and what is left today (T1-37), so the
    dose form can warn while typing. As often as the zone, since a finished dose changes it. */
export function useDoseLimits(zoneId: string) {
  const pollMs = usePollMs(ONE_MINUTE);
  return useQuery({
    queryKey: ["dose-limits", zoneId],
    queryFn: () => getJson<DoseLimits>(`/zones/${zoneId}/dose-limits`),
    refetchInterval: pollMs,
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

/* ---------- Simulator control, playground sites only (§11.4) ---------- */

const SIM_STATE = ["sim", "state"] as const;

/** 404 from /api/sim: the site has no playground (T1-20), so there is nothing to ask again. */
export const noSimulator = (error: unknown) => error instanceof ApiError && error.status === 404;

/** GET /api/sim/state · speed, simulated clock, running drills, held values. `enabled` is false
    on a real farm, which has no simulator to ask. */
export function useSimState(enabled = true) {
  return useQuery({
    queryKey: SIM_STATE,
    queryFn: () => getJson<SimState>("/sim/state"),
    refetchInterval: (query) => (noSimulator(query.state.error) ? false : PLAYGROUND_POLL_MS),
    enabled,
  });
}

/** GET /api/sim/scenario-options · the drill catalogue and the holdable ranges (scenarios.json). */
export function useScenarioOptions(enabled = true) {
  return useQuery({
    queryKey: ["sim", "scenario-options"],
    queryFn: () => getJson<ScenarioOptions>("/sim/scenario-options"),
    staleTime: 5 * ONE_MINUTE,
    enabled,
  });
}

/** What the dashboard shows of the farm: asked again after a simulator action. */
const FARM_READINGS = [["zone"], ["climate"], ["power"]] as const;

/** A simulator action. §11.4 doesn't fix what these answer, so ask for the sim state again
    afterwards, and for the farm's readings too: a drill, hold or reset changes what the zone
    cards, climate and power show. The screen, the banner and the dashboard then show the change. */
function useSimAction<V>(send: (vars: V) => Promise<unknown>) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: send,
    onSuccess: () =>
      Promise.all(
        [SIM_STATE, ...FARM_READINGS].map((queryKey) => queryClient.invalidateQueries({ queryKey })),
      ),
  });
}

/** The refusal of a card's last simulator action, so an older one can't hide a newer one. Its
    message is the server's (§11.1), shown as it came. */
export function latestError(...actions: { error: Error | null; submittedAt: number }[]) {
  const last = actions.reduce((a, b) => (b.submittedAt > a.submittedAt ? b : a));
  return last.error;
}

export const useSimSpeed = () => useSimAction((x: SimSpeed) => postJson("/sim/speed", { x }));

/** Trigger (`on: true`) or Fix (`on: false`) a failure drill. */
export const useSimFault = () => useSimAction((body: SimFaultBody) => postJson("/sim/faults", body));

export const useSimHold = () => useSimAction((hold: SimHold) => postJson("/sim/holds", hold));

export const useSimRelease = () =>
  useSimAction(({ target, metric }: { target: string; metric: string }) =>
    deleteJson(`/sim/holds/${encodeURIComponent(target)}/${encodeURIComponent(metric)}`),
  );

export const useSimReleaseAll = () => useSimAction(() => postJson("/sim/holds/release-all"));

/** Owner only: back to the start of the scenario, all drills off and every value released. */
export const useSimReset = () => useSimAction(() => postJson("/sim/reset"));

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

// The controller reports the new zone state a moment after the server accepts Stop or Resume.
const ZONE_FOLLOW_UP_MS = [2_000, 5_000];

function refreshZoneSoon(queryClient: ReturnType<typeof useQueryClient>, zone: string) {
  void queryClient.invalidateQueries({ queryKey: ["zone", zone] });
  for (const ms of ZONE_FOLLOW_UP_MS)
    setTimeout(() => void queryClient.invalidateQueries({ queryKey: ["zone", zone] }), ms);
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
      refreshZoneSoon(queryClient, zone);
    },
  });
}

/** Resume a stopped zone once a person has checked it (§7.8). Nothing restarts by itself. */
export function useResumeZone() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (zone: string) => postJson<{ cmdId: string; state: string }>("/resume", { zone }),
    onSuccess: (_answer, zone) => refreshZoneSoon(queryClient, zone),
  });
}
