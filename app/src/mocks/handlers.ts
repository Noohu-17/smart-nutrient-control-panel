// src/mocks/handlers.ts · T2-4 · the fake API. MSW answers these calls inside the browser (and in tests).
// Shapes follow the real back end (T1-12): turn the fake API off with VITE_USE_MOCKS=false.
import { delay, http, HttpResponse } from "msw";
import type {
  ApiErrorBody,
  DoseConfirmed,
  DoseRequest,
  DoseRequestBody,
  Farm,
  ZoneStatus,
} from "../api/types";
import { buildClimateTelemetry, buildPowerState } from "./data/climatePower";
import { zoneDosing } from "./data/dosing";
import { farmLayout } from "./data/layout";
import { buildZoneStatus, findZone } from "./data/telemetry";
import { DoseInputError, runDoseChecks, validateLines } from "./dosing";
import { currentScenario } from "./scenario";

/** Error body in the contract §11.1 shape. */
const apiError = (status: number, code: string, message: string) =>
  HttpResponse.json<ApiErrorBody>({ error: { code, message } }, { status });

/** A short wait so loading states are visible; long in the "slow" scenario. */
const networkDelay = () => delay(currentScenario() === "slow" ? 3000 : 300);

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
  if (zoneId === "Z1" && scenario === "z1-estop") status.estop = true;
  return status;
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
    return HttpResponse.json(sent, { status: 202 });
  }),
];
