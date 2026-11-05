/* The fake simulator behind /api/sim (contract §11.4). It keeps one playground farm's speed,
   simulated clock, running drills and held values, and refuses what the real one would (422).
   scenarios.json is a copy of config/clients/client_A/scenarios.json; handlers.test.ts fails if
   they drift apart. A fake drill or hold changes only this state, not the fake telemetry. */
import type { ScenarioOptions, SimFaultBody, SimHold, SimState } from "../api/types";
import { drillTargets, holdMetrics, holdTargets, metricLabel, SPEEDS } from "../config/sim";
import { farmLayout } from "./data/layout";
import scenariosJson from "./data/scenarios.json";

/** What GET /sim/scenario-options answers: scenarios.json without its file header. */
export const scenarioOptions = Object.fromEntries(
  Object.entries(scenariosJson).filter(([key]) => key !== "v" && key !== "_meta"),
) as unknown as ScenarioOptions;

export class SimInputError extends Error {}

// the run every reset goes back to: a July morning at the default speed
const START = { scenario: "Default farm", simTime: Date.parse("2026-07-01T06:00:00+05:30"), speed: 60 };

let sim: {
  speed: SimState["speed"];
  simMs: number;
  realMs: number;
  faults: SimState["faults"];
  holds: SimHold[];
};

/** Back to the start of the scenario: all drills off, every value released (POST /sim/reset). */
export function resetSim() {
  sim = {
    speed: START.speed as SimState["speed"],
    simMs: START.simTime,
    realMs: Date.now(),
    faults: [],
    holds: [],
  };
}
resetSim();

/** Move the simulated clock on by the real time passed, at the current speed. */
function tick(now = Date.now()) {
  sim.simMs += (now - sim.realMs) * sim.speed;
  sim.realMs = now;
}

export function simState(): SimState {
  tick();
  return {
    scenario: START.scenario,
    simTime: new Date(sim.simMs).toISOString(),
    speed: sim.speed,
    weather: "CLEAR",
    faults: sim.faults.map((f) => ({ ...f })),
    holds: sim.holds.map((h) => ({ ...h })),
  };
}

export function setSpeed(x: unknown) {
  if (!SPEEDS.includes(x as SimState["speed"]))
    throw new SimInputError(`Speed must be one of ${SPEEDS.join(", ")}`);
  tick();
  sim.speed = x as SimState["speed"];
}

export function setFault({ kind, target, on }: SimFaultBody) {
  const drill = scenarioOptions.failureDrills.find((d) => d.kind === kind);
  if (!drill) throw new SimInputError(`There is no failure drill ${kind}`);
  if (!drillTargets(farmLayout, drill).includes(target))
    throw new SimInputError(`${drill.title} can’t be aimed at ${target}`);
  sim.faults = sim.faults.filter((f) => !(f.kind === kind && f.target === target));
  if (on) sim.faults.push({ kind, target });
}

export function hold({ target, metric, value, gradual = true }: SimHold) {
  if (!holdTargets(farmLayout).includes(target)) throw new SimInputError(`There is no ${target} to hold`);
  const spec = holdMetrics(farmLayout, scenarioOptions.holdable, target).find((m) => m.metric === metric);
  if (!spec) throw new SimInputError(`${metricLabel(metric)} can’t be held on ${target}`);
  const { min, max } = spec.range;
  if (typeof value !== "number" || !Number.isFinite(value) || value < min || value > max)
    throw new SimInputError(`${metricLabel(metric)} must be between ${min} and ${max}`);
  release(target, metric);
  sim.holds.push({ target, metric, value, gradual });
}

/** false when nothing was held there. */
export function release(target: string, metric: string) {
  const before = sim.holds.length;
  sim.holds = sim.holds.filter((h) => !(h.target === target && h.metric === metric));
  return sim.holds.length < before;
}

export function releaseAll() {
  sim.holds = [];
}
