// src/mocks/sim.test.ts · the fake simulator control API (contract §11.4) keeps state and refuses bad values
import { describe, expect, it } from "vitest";
import configScenarios from "../../config/clients/client_A/scenarios.json";
import type { ApiErrorBody, ScenarioOptions, SimState } from "../api/types";

const send = (method: string, path: string, body?: unknown) =>
  fetch(`/api${path}`, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
const state = async () => (await (await send("GET", "/sim/state")).json()) as SimState;
const errorOf = async (res: Response) => ((await res.json()) as ApiErrorBody).error;

describe("fake simulator (/api/sim)", () => {
  it("starts at ×60 with nothing held and no drill running", async () => {
    const s = await state();
    expect(s).toMatchObject({ speed: 60, faults: [], holds: [] });
    expect(Number.isNaN(Date.parse(s.simTime))).toBe(false);
  });

  it("GET /sim/scenario-options answers the drills and ranges of scenarios.json", async () => {
    const options = (await (await send("GET", "/sim/scenario-options")).json()) as ScenarioOptions;
    expect(options.failureDrills).toEqual(configScenarios.failureDrills);
    expect(options.holdable).toEqual(configScenarios.holdable);
    expect(options).not.toHaveProperty("_meta");
  });

  it("sets the speed to 0, 1, 60 or 288 and refuses anything else with 422", async () => {
    expect((await send("POST", "/sim/speed", { x: 288 })).status).toBe(200);
    expect((await state()).speed).toBe(288);
    const res = await send("POST", "/sim/speed", { x: 5 });
    expect(res.status).toBe(422);
    expect((await errorOf(res)).code).toBe("VALIDATION_ERROR");
  });

  it("triggers and fixes a drill, and refuses an unknown drill or target", async () => {
    await send("POST", "/sim/faults", { kind: "LEAK", target: "Z2", on: true });
    expect((await state()).faults).toEqual([{ kind: "LEAK", target: "Z2" }]);
    await send("POST", "/sim/faults", { kind: "LEAK", target: "Z2", on: false });
    expect((await state()).faults).toEqual([]);
    expect((await send("POST", "/sim/faults", { kind: "NOPE", target: "Z1", on: true })).status).toBe(422);
    expect((await send("POST", "/sim/faults", { kind: "LEAK", target: "Z9", on: true })).status).toBe(422);
  });

  it("holds a value inside the scenarios.json range, and releases one or all", async () => {
    await send("POST", "/sim/holds", { target: "Z1", metric: "ph", value: 7.2, gradual: true });
    await send("POST", "/sim/holds", { target: "POLYHOUSE", metric: "airTempC", value: 38, gradual: true });
    await send("POST", "/sim/holds", { target: "Z2", metric: "dayTankPct.ACID", value: 5, gradual: false });
    expect((await state()).holds).toHaveLength(3);

    expect((await send("DELETE", "/sim/holds/Z1/ph")).status).toBe(200);
    expect((await state()).holds.map((h) => h.metric)).toEqual(["airTempC", "dayTankPct.ACID"]);
    expect((await send("DELETE", "/sim/holds/Z1/ph")).status).toBe(404);

    await send("POST", "/sim/holds/release-all");
    expect((await state()).holds).toEqual([]);
  });

  it("refuses a value outside the range, or a reading the target can't hold", async () => {
    const tooHigh = await send("POST", "/sim/holds", {
      target: "Z1",
      metric: "ph",
      value: 15,
      gradual: true,
    });
    expect(tooHigh.status).toBe(422);
    expect((await errorOf(tooHigh)).message).toBe("pH must be between 0 and 14");
    const wrongPlace = { target: "POLYHOUSE", metric: "ph", value: 6, gradual: true };
    expect((await send("POST", "/sim/holds", wrongPlace)).status).toBe(422);
  });

  it("reset goes back to the start: ×60, drills off, values released", async () => {
    await send("POST", "/sim/speed", { x: 0 });
    await send("POST", "/sim/faults", { kind: "HEATWAVE", target: "POLYHOUSE", on: true });
    await send("POST", "/sim/holds", { target: "Z1", metric: "ec", value: 3, gradual: true });
    await send("POST", "/sim/reset");
    expect(await state()).toMatchObject({ speed: 60, faults: [], holds: [] });
  });
});
