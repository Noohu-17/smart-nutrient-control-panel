// src/mocks/handlers.test.ts · T2-4 · the fake API answers like the contract says
import { describe, expect, it } from "vitest";
import configFeatures from "../../config/clients/client_A/features.json";
import configLayout from "../../config/clients/client_A/layout.json";
import configLimits from "../../config/clients/client_A/limits.json";
import configScenarios from "../../config/clients/client_A/scenarios.json";
import type { ClimateTelemetry, Farm, PowerState, ZoneStatus } from "../api/types";
import mockFeatures from "./data/features.json";
import mockLayout from "./data/layout.json";
import mockLimits from "./data/limits.json";
import mockScenarios from "./data/scenarios.json";

// Fields of the zone response example under GET /zones/{zoneId} in docs/contract.md §11.2 (T1-10),
// which the real back end (T1-12) answers. simTs is left out: playground readings only.
const ZONE_FIELDS = [
  "zone",
  "online",
  "lastSeen",
  "ts",
  "readings",
  "quality",
  "equipment",
  "mode",
  "estop",
  "leak",
  "stopped",
  "activeCmdId",
  "operation",
  "latches",
  "configVersion",
];
const READING_FIELDS = [
  "ec",
  "ph",
  "solTempC",
  "reservoirPct",
  "reservoirL",
  "circFlowLpm",
  "doMgL",
  "dayTankPct",
  "dayTankL",
];

const get = (path: string) => fetch(`/api${path}`);

describe("fake API (MSW)", () => {
  it("GET /api/health says ok", async () => {
    const res = await get("/health");
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ status: "OK", database: "UP", broker: "UP" });
  });

  it("GET /api/farm returns Zone A and Zone B from layout.json", async () => {
    const farm = (await (await get("/farm")).json()) as Farm;
    expect(farm.site).toBe("siet-ph1");
    expect(farm.zones.map((z) => [z.id, z.name])).toEqual([
      ["Z1", "Zone A"],
      ["Z2", "Zone B"],
    ]);
  });

  it("the mock layout is an exact copy of config/clients/client_A/layout.json", () => {
    expect(mockLayout).toEqual(configLayout);
  });

  it("GET /api/farm carries the features, a copy of config/clients/client_A/features.json", async () => {
    const farm = (await (await get("/farm")).json()) as Farm;
    expect(farm.features).toEqual(configFeatures);
    expect(mockFeatures).toEqual(configFeatures);
  });

  it("the mock limits are an exact copy of config/clients/client_A/limits.json", () => {
    expect(mockLimits).toEqual(configLimits);
  });

  it("the mock scenarios are an exact copy of config/clients/client_A/scenarios.json", () => {
    expect(mockScenarios).toEqual(configScenarios);
  });

  it("GET /api/zones/Z1 has exactly the fields of the contract §11.2 zone response (T1-10)", async () => {
    const res = await get("/zones/Z1");
    expect(res.status).toBe(200);
    const t = (await res.json()) as ZoneStatus;

    expect(Object.keys(t).sort()).toEqual([...ZONE_FIELDS].sort());
    expect(Object.keys(t.readings).sort()).toEqual([...READING_FIELDS].sort());
    expect(t).toMatchObject({ zone: "Z1", online: true, mode: "REMOTE", stopped: false, latches: [] });
    expect(new Date(t.ts!).toISOString()).toBe(t.ts); // ISO-8601 UTC with milliseconds and Z
    expect(t.lastSeen).toBe(t.ts);
    // first answer is the contract's own example
    expect(t.readings).toMatchObject({
      ec: 1.62,
      ph: 6.04,
      solTempC: 23.4,
      reservoirPct: 78,
      reservoirL: 936,
      circFlowLpm: 31,
    });
    expect(t.readings.doMgL).toBeNull(); // no DO sensor fitted in the layout
    expect(t.readings.dayTankL).toEqual({ A: 32, B: 33, ACID: 40.5 });
    expect(t.quality).toEqual({ ec: "OK", ph: "OK", solTempC: "OK", reservoirPct: "OK", circFlowLpm: "OK" });
    expect(Object.keys(t.equipment)).toEqual([
      "Z1-CIRC",
      "Z1-REFILL",
      "Z1-DOSE-A",
      "Z1-DOSE-B",
      "Z1-DOSE-ACID",
    ]);
    expect(t).toMatchObject({ estop: false, leak: false, activeCmdId: null, operation: null });
  });

  it("GET /api/zones lists every zone in layout order", async () => {
    const res = await get("/zones");
    expect(res.status).toBe(200);
    const { items } = (await res.json()) as { items: ZoneStatus[] };
    expect(items.map((z) => z.zone)).toEqual(["Z1", "Z2"]);
  });

  it("readings move a little between requests, as live data would", async () => {
    const first = (await (await get("/zones/Z1")).json()) as ZoneStatus;
    const second = (await (await get("/zones/Z1")).json()) as ZoneStatus;
    expect(Math.abs(second.readings.ec! - first.readings.ec!)).toBeLessThan(0.05);
  });

  it("GET /api/climate returns telemetry shaped like contract §6.2, positions from the layout", async () => {
    const res = await get("/climate");
    expect(res.status).toBe(200);
    const c = (await res.json()) as ClimateTelemetry;
    expect(c).toMatchObject({
      v: 1,
      type: "CLIMATE_TELEMETRY",
      site: "siet-ph1",
      device: "CC1",
      mode: "MANUAL",
    });
    // first answer is the contract example
    expect(c.readings).toEqual({
      airTempC: 31.2,
      airTempByPosC: { PAD_END: 29.4, MIDDLE: 31.0, FAN_END: 33.1 },
      airRh: 71,
      outsideTempC: 33.8,
      outsideRh: 58,
      elecRoomTempC: 34.0,
    });
    expect(c.equipment).toMatchObject({
      fanStage: 2,
      FAN1: "ON",
      FAN3: "OFF",
      PAD1: "ON",
      CH1: { state: "ON", setpointC: 22 },
    });
    expect(c.setpoints).toEqual({ airTempMaxC: 30, airRhMax: 85, solTempMaxC: 24 });
  });

  it("GET /api/power returns the state shaped like contract §6.3", async () => {
    const res = await get("/power");
    expect(res.status).toBe(200);
    const p = (await res.json()) as PowerState;
    expect(p).toMatchObject({
      v: 1,
      type: "POWER_STATE",
      device: "PM1",
      mains: "OK",
      phases: { l1V: 231, l2V: 229, l3V: 233 },
      source: "MAINS",
      generator: "STOPPED",
      ats: "MAINS",
      ups: { onBattery: false, batteryPct: 100, runtimeMin: 42 },
    });
  });

  it("GET /api/zones/Z9 is a 404 with the contract error body", async () => {
    const res = await get("/zones/Z9");
    expect(res.status).toBe(404);
    expect(await res.json()).toEqual({ error: { code: "NOT_FOUND", message: "There is no zone Z9" } });
  });
});
