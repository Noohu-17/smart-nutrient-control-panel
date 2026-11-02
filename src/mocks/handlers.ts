// src/mocks/handlers.ts · T2-4 · the fake API. MSW answers these calls inside the browser (and in tests).
import { delay, http, HttpResponse } from "msw";
import type { ApiErrorBody, DoseConfirmed, DoseRequest, DoseRequestBody } from "../api/types";
import { zoneDosing } from "./data/dosing";
import { farmLayout } from "./data/layout";
import { buildZoneTelemetry, findZone } from "./data/telemetry";
import { DoseInputError, runDoseChecks, validateLines } from "./dosing";
import { currentScenario } from "./scenario";

/** Error body in the contract §11.1 shape. */
const apiError = (status: number, code: string, message: string) =>
  HttpResponse.json<ApiErrorBody>({ error: { code, message } }, { status });

/** A short wait so loading states are visible; long in the "slow" scenario. */
const networkDelay = () => delay(currentScenario() === "slow" ? 3000 : 300);

/** The zone's latest telemetry with the chosen scenario applied. */
function zoneTelemetry(zoneId: string) {
  const scenario = currentScenario();
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
  return telemetry;
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
    return HttpResponse.json(farmLayout);
  }),

  // GET /api/zones/:zoneId · latest telemetry for one zone (contract §6.1)
  http.get("/api/zones/:zoneId", async ({ params }) => {
    await networkDelay();
    const zoneId = String(params.zoneId);
    const zone = findZone(zoneId);
    if (!zone) return apiError(404, "NOT_FOUND", `There is no zone ${zoneId}`);

    if (currentScenario() === "z2-offline" && zoneId === "Z2") {
      return apiError(503, "DEVICE_OFFLINE", `Zone controller ${zone.controller} is not reporting`);
    }

    return HttpResponse.json(zoneTelemetry(zoneId));
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
    const telemetry = zoneTelemetry(body.zone);
    if (currentScenario() === "z2-offline" && body.zone === "Z2") {
      telemetry.ts = new Date(Date.now() - 5 * 60_000).toISOString();
    }
    const dr = runDoseChecks(`dr_${crypto.randomUUID()}`, zone, telemetry, dosing, body.lines);
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
