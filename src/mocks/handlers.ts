// src/mocks/handlers.ts · T2-4 · the fake API. MSW answers these calls inside the browser (and in tests).
// Shapes follow the real back end (T1-12): turn the fake API off with VITE_USE_MOCKS=false.
import { delay, http, HttpResponse } from "msw";
import type {
  ApiErrorBody,
  DoseConfirmed,
  DoseRequest,
  DoseRequestBody,
  Farm,
  SimFaultBody,
  SimHold,
  ZoneStatus,
} from "../api/types";
import {
  activeCommandId,
  addCommand,
  commandAt,
  findCommand,
  isZoneStopped,
  resumeZone,
  stopZoneCommands,
  type CommandEnding,
} from "./commands";
import { buildClimateTelemetry, buildPowerState } from "./data/climatePower";
import { zoneDosing } from "./data/dosing";
import { farmLayout } from "./data/layout";
import { buildZoneStatus, findZone } from "./data/telemetry";
import { DoseInputError, runDoseChecks, validateLines } from "./dosing";
import { currentScenario } from "./scenario";
import {
  hold,
  release,
  releaseAll,
  resetSim,
  scenarioOptions,
  setFault,
  setSpeed,
  SimInputError,
  simState,
  simulateZone,
} from "./sim";

/** Error body in the contract §11.1 shape. */
const apiError = (status: number, code: string, message: string) =>
  HttpResponse.json<ApiErrorBody>({ error: { code, message } }, { status });

/** A short wait so loading states are visible; long in the "slow" scenario. */
const networkDelay = () => delay(currentScenario() === "slow" ? 3000 : 300);

/** How a new dose ends in each ?mock= scenario. */
function commandEnding(): CommandEnding {
  const endings: Partial<Record<string, CommandEnding>> = {
    "dose-blocked": "REJECTED",
    "dose-failed": "FAILED",
    "dose-partial": "PARTIAL",
    "dose-unknown": "UNKNOWN",
    "dose-expired": "EXPIRED",
  };
  return endings[currentScenario()] ?? "COMPLETED";
}

/** The layout this scenario's farm has: "no-generator" is a site without a generator or UPS. */
function scenarioLayout(): Farm {
  if (currentScenario() === "no-generator")
    return { ...farmLayout, power: { ...farmLayout.power, generator: false, ups: false } };
  return farmLayout;
}

const minutesAgo = (minutes: number) => new Date(Date.now() - minutes * 60_000).toISOString();

const ALL_STALE: ZoneStatus["quality"] = {
  ec: "STALE",
  ph: "STALE",
  solTempC: "STALE",
  reservoirPct: "STALE",
  circFlowLpm: "STALE",
};

/** One zone as the current scenario has it. */
function zoneStatus(zoneId: string): ZoneStatus {
  const scenario = currentScenario();
  const status = buildZoneStatus(zoneId)!;
  status.activeCmdId = activeCommandId(zoneId);
  status.stopped = isZoneStopped(zoneId);
  simulateZone(status); // drills and held values from the Simulator screen
  if (zoneId === "Z2" && scenario === "z2-offline") {
    // §11.2: an offline zone still answers 200, with its last values marked old
    status.online = false;
    status.lastSeen = minutesAgo(4);
    status.ts = minutesAgo(4.5);
    status.quality = ALL_STALE;
  }
  if (zoneId === "Z2" && scenario === "z2-sensor-fault") {
    status.readings.ec = null; // contract §1: a failed sensor sends null, never a fake 0
    status.quality.ec = "FAULT";
  }
  if (zoneId === "Z2" && scenario === "z2-stale") {
    // the controller still sends heartbeats, but its last reading is past the fresh limit
    status.ts = minutesAgo(5);
    status.quality = ALL_STALE;
  }
  if (zoneId === "Z2" && scenario === "z2-reservoir-low") {
    status.readings.reservoirPct = 22;
    status.readings.reservoirL = Math.round(0.22 * findZone(zoneId)!.reservoir.usableL);
  }
  if (zoneId === "Z1" && scenario === "z1-ec-stuck") {
    // the simulator's STUCK_READING drill: the probe repeats one value, marked STALE (§6.1 has no STUCK)
    status.readings.ec = 1.1;
    status.quality.ec = "STALE";
  }
  if (zoneId === "Z1" && scenario === "z1-estop") {
    status.estop = true;
    status.stopped = true; // an E-STOP stops the zone until a person resumes it (§6.8)
  }
  if (zoneId === "Z1" && scenario === "z1-leak") status.leak = true;
  return status;
}

/** Run a simulator action and answer with the new sim state; a refused value is 422 (§11.1).
    A real farm has no simulator, so every /sim call is 404 there (§11.4: playground only). */
async function simAction(act: () => void) {
  await networkDelay();
  if (currentScenario() === "no-playground") return apiError(404, "NOT_FOUND", "This site has no simulator");
  try {
    act();
  } catch (e) {
    if (e instanceof SimInputError) return apiError(422, "VALIDATION_ERROR", e.message);
    throw e;
  }
  return HttpResponse.json(simState());
}

// dose requests reviewed so far, and the answers already given to a confirm (by Idempotency-Key)
const doseRequests = new Map<string, DoseRequest>();
const confirmed = new Map<string, DoseConfirmed>();

export const handlers = [
  // GET /api/health · uptime check (contract §11.2, public)
  http.get("/api/health", () =>
    currentScenario() === "api-degraded"
      ? HttpResponse.json({ status: "DEGRADED", database: "DOWN", broker: "UP" }, { status: 503 })
      : HttpResponse.json({ status: "OK", database: "UP", broker: "UP" }),
  ),

  // GET /api/farm · layout (contract §2.4). Zone names come from here, never from screen code.
  http.get("/api/farm", async () => {
    await networkDelay();
    if (currentScenario() === "farm-error")
      return apiError(500, "INTERNAL", "The farm layout could not be loaded");
    if (currentScenario() === "no-playground")
      return HttpResponse.json({ ...farmLayout, features: { ...farmLayout.features, playground: false } });
    return HttpResponse.json(scenarioLayout());
  }),

  // GET /api/climate · latest climate telemetry from CC1 (contract §6.2)
  http.get("/api/climate", async () => {
    await networkDelay();
    return HttpResponse.json(buildClimateTelemetry(scenarioLayout()));
  }),

  // GET /api/power · latest power state from PM1 (contract §6.3)
  http.get("/api/power", async () => {
    await networkDelay();
    const power = buildPowerState(scenarioLayout());
    if (currentScenario() === "mains-lost") {
      power.mains = "LOST";
      power.phases = { l1V: null, l2V: null, l3V: null }; // §6.3: a lost phase is null, never 0
      power.source = "GENERATOR";
      power.ats = "GENERATOR";
      if (power.generator) power.generator = "RUNNING";
      if (power.ups) power.ups = { onBattery: true, batteryPct: 87, runtimeMin: 36 };
    }
    return HttpResponse.json(power);
  }),

  // GET /api/zones · every zone, in layout order (contract §11.2)
  http.get("/api/zones", async () => {
    await networkDelay();
    return HttpResponse.json({ items: farmLayout.zones.map((zone) => zoneStatus(zone.id)) });
  }),

  // GET /api/zones/:zoneId · one zone's latest readings and state (contract §11.2)
  http.get("/api/zones/:zoneId", async ({ params }) => {
    await networkDelay();
    const zoneId = String(params.zoneId);
    if (!findZone(zoneId)) return apiError(404, "NOT_FOUND", `There is no zone ${zoneId}`);
    return HttpResponse.json(zoneStatus(zoneId));
  }),

  // POST /api/dose-requests · run the safety checks; nothing is sent to the farm (contract §11.3)
  http.post("/api/dose-requests", async ({ request }) => {
    await networkDelay();
    const body = (await request.json()) as DoseRequestBody;
    const zone = findZone(body.zone);
    const dosing = zoneDosing(body.zone);
    if (!zone || !dosing) return apiError(404, "NOT_FOUND", `There is no zone ${body.zone}`);
    try {
      validateLines(zone, body.lines);
    } catch (e) {
      if (e instanceof DoseInputError) return apiError(422, "INVALID_COMMAND", e.message);
      throw e;
    }
    const dr = runDoseChecks(`dr_${crypto.randomUUID()}`, zone, zoneStatus(body.zone), dosing, body.lines);
    doseRequests.set(dr.id, dr);
    return HttpResponse.json(dr, { status: 201 });
  }),

  // POST /api/dose-requests/:id/confirm · create and dispatch the command (contract §11.3)
  http.post("/api/dose-requests/:id/confirm", async ({ params, request }) => {
    await networkDelay();
    const key = request.headers.get("Idempotency-Key");
    const repeat = key ? confirmed.get(key) : undefined;
    if (repeat) return HttpResponse.json(repeat, { status: 202 });

    const dr = doseRequests.get(String(params.id));
    if (!dr) return apiError(404, "NOT_FOUND", `There is no dose request ${String(params.id)}`);
    const blocking = dr.checks.find((c) => c.result === "BLOCK");
    if (blocking) return apiError(409, blocking.id, blocking.message);

    const sent: DoseConfirmed = { cmdId: crypto.randomUUID(), state: "DISPATCHED" };
    if (key) confirmed.set(key, sent);
    const { readings } = buildZoneStatus(dr.zone)!;
    addCommand({
      cmdId: sent.cmdId,
      dr,
      device: findZone(dr.zone)!.controller,
      before: { ec: readings.ec, ph: readings.ph },
      ending: commandEnding(),
      createdAt: Date.now(),
    });
    return HttpResponse.json(sent, { status: 202 });
  }),

  // GET /api/sim/state · speed, simulated clock, running drills, held values (§11.4)
  http.get("/api/sim/state", () => simAction(() => {})),

  // GET /api/sim/scenario-options · the client's scenarios.json (§11.4.1)
  http.get("/api/sim/scenario-options", async () => {
    await networkDelay();
    if (currentScenario() === "no-playground")
      return apiError(404, "NOT_FOUND", "This site has no simulator");
    return HttpResponse.json(scenarioOptions);
  }),

  // POST /api/sim/speed { x } · 0 (pause), 1, 60 or 288
  http.post("/api/sim/speed", async ({ request }) => {
    const { x } = (await request.json()) as { x: unknown };
    return simAction(() => setSpeed(x));
  }),

  // POST /api/sim/faults { kind, target, on } · Trigger or Fix a failure drill (§11.4.3)
  http.post("/api/sim/faults", async ({ request }) => {
    const body = (await request.json()) as SimFaultBody;
    return simAction(() => setFault(body));
  }),

  // POST /api/sim/holds { target, metric, value, gradual } · set a value and hold it (§11.4.2)
  http.post("/api/sim/holds", async ({ request }) => {
    const body = (await request.json()) as SimHold;
    return simAction(() => hold(body));
  }),

  // POST /api/sim/holds/release-all
  http.post("/api/sim/holds/release-all", () => simAction(releaseAll)),

  // DELETE /api/sim/holds/:target/:metric · release one held value
  http.delete("/api/sim/holds/:target/:metric", async ({ params }) => {
    const [target, metric] = [String(params.target), String(params.metric)];
    if (!simState().holds.some((h) => h.target === target && h.metric === metric))
      return apiError(404, "NOT_FOUND", `Nothing is held on ${target} ${metric}`);
    return simAction(() => release(target, metric));
  }),

  // POST /api/sim/reset · Owner only on the real back end; back to the start of the scenario
  http.post("/api/sim/reset", () => simAction(resetSim)),

  // GET /api/commands/:cmdId · the command with its state and lines (contract §6.6, §8)
  http.get("/api/commands/:cmdId", async ({ params }) => {
    await networkDelay();
    const record = findCommand(String(params.cmdId));
    if (!record) return apiError(404, "NOT_FOUND", `There is no command ${String(params.cmdId)}`);
    return HttpResponse.json(commandAt(record));
  }),

  // POST /api/stop · never checked and never refused (contract §7.4, PRD rule 1)
  http.post("/api/stop", async ({ request }) => {
    const body = (await request.json()) as { scope: string; zone?: string };
    for (const zone of farmLayout.zones) {
      if (body.scope !== "ZONE" || body.zone === zone.id) stopZoneCommands(zone.id);
    }
    return HttpResponse.json({ cmdId: crypto.randomUUID(), state: "DISPATCHED" }, { status: 202 });
  }),

  // POST /api/resume { zone } · a person has checked the zone; nothing restarts by itself (§7.8)
  http.post("/api/resume", async ({ request }) => {
    const body = (await request.json()) as { zone?: string };
    if (!body.zone || !findZone(body.zone)) return apiError(404, "NOT_FOUND", `There is no zone ${body.zone}`);
    if (zoneStatus(body.zone).estop)
      return apiError(409, "ESTOP", "The E-STOP is still pressed. Release it first");
    resumeZone(body.zone);
    return HttpResponse.json({ cmdId: crypto.randomUUID(), state: "DISPATCHED" }, { status: 202 });
  }),
];
