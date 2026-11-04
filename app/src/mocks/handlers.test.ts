// src/mocks/handlers.test.ts · T2-4 · the fake API answers like the contract says
import { describe, expect, it } from "vitest";
import configFeatures from "../../../config/clients/client_A/features.json";
import configLayout from "../../../config/clients/client_A/layout.json";
import type { Farm, ZoneStatus } from "../api/types";
import mockFeatures from "./data/features.json";
import mockLayout from "./data/layout.json";

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

  it("GET /api/zones/Z9 is a 404 with the contract error body", async () => {
    const res = await get("/zones/Z9");
    expect(res.status).toBe(404);
    expect(await res.json()).toEqual({ error: { code: "NOT_FOUND", message: "There is no zone Z9" } });
  });
});
