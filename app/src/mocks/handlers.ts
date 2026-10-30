// src/mocks/handlers.ts · T2-4 · the fake API. MSW answers these calls inside the browser (and in tests).
import { delay, http, HttpResponse } from "msw";
import type { ApiErrorBody } from "../api/types";
import { farmLayout } from "./data/layout";
import { buildZoneTelemetry, findZone } from "./data/telemetry";
import { currentScenario } from "./scenario";

/** Error body in the contract §11.1 shape. */
const apiError = (status: number, code: string, message: string) =>
  HttpResponse.json<ApiErrorBody>({ error: { code, message } }, { status });

/** A short wait so loading states are visible; long in the "slow" scenario. */
const networkDelay = () => delay(currentScenario() === "slow" ? 3000 : 300);

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
    return HttpResponse.json(farmLayout);
  }),

  // GET /api/zones/:zoneId · latest telemetry for one zone (contract §6.1)
  http.get("/api/zones/:zoneId", async ({ params }) => {
    await networkDelay();
    const zoneId = String(params.zoneId);
    const zone = findZone(zoneId);
    if (!zone) return apiError(404, "NOT_FOUND", `There is no zone ${zoneId}`);

    const scenario = currentScenario();
    if (scenario === "z2-offline" && zoneId === "Z2") {
      return apiError(503, "DEVICE_OFFLINE", `Zone controller ${zone.controller} is not reporting`);
    }

    const telemetry = buildZoneTelemetry(zoneId)!;
    if (zoneId === "Z2" && scenario === "z2-sensor-fault") {
      telemetry.readings.ec = null; // contract §1: a failed sensor sends null, never a fake 0
      telemetry.quality.ec = "FAULT";
    }
    if (zoneId === "Z2" && scenario === "z2-stale") {
      telemetry.ts = new Date(Date.now() - 5 * 60_000).toISOString();
      telemetry.quality = {
        ec: "STALE",
        ph: "STALE",
        solTempC: "STALE",
        reservoirPct: "STALE",
        circFlowLpm: "STALE",
      };
    }
    if (zoneId === "Z1" && scenario === "z1-estop") telemetry.estop = true;
    return HttpResponse.json(telemetry);
  }),
];
