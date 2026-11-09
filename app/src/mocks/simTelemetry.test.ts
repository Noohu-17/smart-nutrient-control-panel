// src/mocks/simTelemetry.test.ts · T2-20 · a drill or held value from the Simulator screen shows in
// the fake zone telemetry, the way the real simulator reports it, and goes away on Fix or Release
import { describe, expect, it } from "vitest";
import type { FailureDrill, ZoneStatus } from "../api/types";
import { farmLayout } from "./data/layout";
import { scenarioOptions } from "./sim";

const send = (method: string, path: string, body?: unknown) =>
  fetch(`/api${path}`, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
const zone = async (id = "Z1") => (await (await send("GET", `/zones/${id}`)).json()) as ZoneStatus;
const drill = (kind: string, target: string, on = true) => send("POST", "/sim/faults", { kind, target, on });

// T4-9 to T4-11 add these to scenarios.json; until that reaches master, this file adds them
// (each test file has its own copy of the modules, so the others still see scenarios.json as it is)
const T4_DRILLS: FailureDrill[] = (
  [
    ["SENSOR_OFFLINE", "ZONE_SENSOR"],
    ["STUCK_READING", "ZONE_SENSOR"],
    ["ESTOP", "ZONE"],
    ["DEVICE_OFFLINE", "DEVICE"],
  ] as const
).map(([kind, targetType]) => ({
  kind,
  targetType,
  group: "TEST",
  prdCode: "",
  title: kind,
  explanation: "",
  fix: "",
  params: {},
}));
const known = new Set(scenarioOptions.failureDrills.map((d) => d.kind));
const added = T4_DRILLS.filter((d) => !known.has(d.kind));
scenarioOptions.failureDrills.push(...added);

describe("drills and holds in the fake zone telemetry", () => {
  it("LEAK: wet until Fix", async () => {
    await drill("LEAK", "Z1");
    expect((await zone()).leak).toBe(true);
    expect((await zone("Z2")).leak).toBe(false);
    await drill("LEAK", "Z1", false);
    expect((await zone()).leak).toBe(false);
  });

  it("a held reservoir level changes the reading until Release", async () => {
    await send("POST", "/sim/holds", { target: "Z2", metric: "reservoirPct", value: 20, gradual: false });
    const held = await zone("Z2");
    expect(held.readings.reservoirPct).toBe(20);
    const usableL = farmLayout.zones.find((z) => z.id === "Z2")!.reservoir.usableL;
    expect(held.readings.reservoirL).toBe(Math.round(0.2 * usableL));
    await send("DELETE", "/sim/holds/Z2/reservoirPct");
    expect((await zone("Z2")).readings.reservoirPct).toBeGreaterThan(30);
  });

  it("SENSOR_OFFLINE: null with FAULT (§6.1)", async () => {
    await drill("SENSOR_OFFLINE", "Z1.ph");
    const t = await zone();
    expect([t.readings.ph, t.quality.ph]).toEqual([null, "FAULT"]);
    expect(t.quality.ec).toBe("OK");
  });

  it("STUCK_READING: the same value, marked STALE", async () => {
    await drill("STUCK_READING", "Z1.ec");
    const first = await zone();
    const later = await zone();
    expect(first.quality.ec).toBe("STALE");
    expect(later.readings.ec).toBe(first.readings.ec);
  });

  it("ESTOP: stops the zone, which stays stopped after the release until Resume", async () => {
    await drill("ESTOP", "Z1");
    expect(await zone()).toMatchObject({ estop: true, stopped: true });
    expect((await send("POST", "/resume", { zone: "Z1" })).status).toBe(409);

    await drill("ESTOP", "Z1", false);
    expect(await zone()).toMatchObject({ estop: false, stopped: true });
    expect((await send("POST", "/resume", { zone: "Z1" })).status).toBe(202);
    expect((await zone()).stopped).toBe(false);
  });

  it("DEVICE_OFFLINE on the zone controller: offline, last values marked STALE", async () => {
    await drill("DEVICE_OFFLINE", "ZC2");
    const t = await zone("Z2");
    expect(t.online).toBe(false);
    expect(new Set(Object.values(t.quality))).toEqual(new Set(["STALE"]));
    expect((await zone("Z1")).online).toBe(true);
  });

  it("reset clears every drill and leaves no zone stopped", async () => {
    await drill("ESTOP", "Z1");
    await send("POST", "/sim/reset");
    expect(await zone()).toMatchObject({ estop: false, stopped: false });
  });
});
