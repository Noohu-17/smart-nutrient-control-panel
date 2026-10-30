// src/mocks/handlers.test.ts · T2-4 · the fake API answers like the contract says
import { describe, expect, it } from "vitest";
import configLayout from "../../../config/clients/client_A/layout.json";
import type { Farm, ZoneTelemetry } from "../api/types";
import mockLayout from "./data/layout.json";

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

  it("GET /api/zones/Z1 returns telemetry shaped like contract §6.1", async () => {
    const res = await get("/zones/Z1");
    expect(res.status).toBe(200);
    const t = (await res.json()) as ZoneTelemetry;

    expect(t).toMatchObject({
      v: 1,
      type: "ZONE_TELEMETRY",
      site: "siet-ph1",
      device: "ZC1",
      zone: "Z1",
      mode: "REMOTE",
    });
    expect(t.msgId).toMatch(/^[0-9a-f-]{36}$/);
    expect(new Date(t.ts).toISOString()).toBe(t.ts); // ISO-8601 UTC with Z
    expect(Object.keys(t.readings).sort()).toEqual(
      [
        "circFlowLpm",
        "dayTankL",
        "dayTankPct",
        "doMgL",
        "ec",
        "ph",
        "reservoirL",
        "reservoirPct",
        "solTempC",
      ].sort(),
    );
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
    expect(t).toMatchObject({ estop: false, leak: false, activeCmdId: null });
  });

  it("readings move a little between requests, as live data would", async () => {
    const first = (await (await get("/zones/Z1")).json()) as ZoneTelemetry;
    const second = (await (await get("/zones/Z1")).json()) as ZoneTelemetry;
    expect(second.seq).toBe(first.seq + 1);
    expect(Math.abs(second.readings.ec! - first.readings.ec!)).toBeLessThan(0.05);
  });

  it("GET /api/zones/Z9 is a 404 with the contract error body", async () => {
    const res = await get("/zones/Z9");
    expect(res.status).toBe(404);
    expect(await res.json()).toEqual({ error: { code: "NOT_FOUND", message: "There is no zone Z9" } });
  });
});
