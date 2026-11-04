// src/mocks/handlers.ts · T2-4 · the fake API. MSW answers these calls inside the browser (and in tests).
// Shapes follow the real back end (T1-12): turn the fake API off with VITE_USE_MOCKS=false.
import { delay, http, HttpResponse } from "msw";
import type { ApiErrorBody, Farm, ZoneStatus } from "../api/types";
import { buildClimateTelemetry, buildPowerState } from "./data/climatePower";
import { farmLayout } from "./data/layout";
import { buildZoneStatus, findZone } from "./data/telemetry";
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
];
