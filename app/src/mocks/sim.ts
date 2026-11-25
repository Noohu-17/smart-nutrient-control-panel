/* The fake simulator behind /api/sim (contract §11.4). It keeps one playground farm's speed,
   simulated clock, running drills and held values, and refuses what the real one would (422).
   scenarios.json is a copy of config/clients/client_A/scenarios.json; handlers.test.ts fails if
   they drift apart. simulateZone shows the running drills and held values in the fake zone
   telemetry, the way the real simulator reports them (T4-9 to T4-11). */
import type { DoseLine, ScenarioOptions, SimFaultBody, SimHold, SimState, ZoneStatus } from "../api/types";
import { drillTargets, holdMetrics, holdTargets, metricLabel, SPEEDS } from "../config/sim";
import { resumeZone, stopZoneCommands } from "./commands";
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

// when each running drill started, and the value a stuck probe keeps sending; by "KIND target"
const drillMemory = new Map<string, { since: number; stuck?: number | null }>();
const drillKey = (kind: string, target: string) => `${kind} ${target}`;

/** Back to the start of the scenario: all drills off, every value released (POST /sim/reset). */
export function resetSim() {
  sim = {
    speed: START.speed as SimState["speed"],
    simMs: START.simTime,
    realMs: Date.now(),
    faults: [],
    holds: [],
  };
  drillMemory.clear();
  // the real reset leaves no zone stopped (T4-11), including one an E-STOP drill stopped
  for (const zone of farmLayout.zones) resumeZone(zone.id);
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
  drillMemory.delete(drillKey(kind, target));
  if (!on) return;
  sim.faults.push({ kind, target });
  drillMemory.set(drillKey(kind, target), { since: Date.now() });
  // an E-STOP stops the zone, and it stays stopped after the release until a person resumes it (§6.8)
  if (kind === "ESTOP") stopZoneCommands(target);
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

type ZoneMetric = keyof ZoneStatus["quality"];

/** Put a held value into the readings, as if the water itself had changed (§11.4.2). */
function holdReading(status: ZoneStatus, usableL: number, metric: string, value: number) {
  const [base, line] = metric.split(".");
  if (base === "dayTankPct" && line) status.readings.dayTankPct[line as DoseLine] = value;
  else if (base in status.readings) status.readings[base as ZoneMetric] = value;
  if (base === "reservoirPct") status.readings.reservoirL = Math.round((value / 100) * usableL);
}

/** One zone's fake telemetry with the running drills and held values applied. A drill the
    catalogue doesn't list can't be triggered, so it never shows up here. */
export function simulateZone(status: ZoneStatus) {
  const zone = farmLayout.zones.find((z) => z.id === status.zone);
  if (!zone) return status;
  for (const h of sim.holds)
    if (h.target === zone.id) holdReading(status, zone.reservoir.usableL, h.metric, h.value);

  for (const { kind, target } of sim.faults) {
    const memory = drillMemory.get(drillKey(kind, target));
    const [zoneId, probe] = target.split(".") as [string, ZoneMetric | undefined];
    if (kind === "LEAK" && zoneId === zone.id) status.leak = true;
    if (kind === "ESTOP" && zoneId === zone.id) status.estop = true;
    if (zoneId === zone.id && probe && probe in status.quality) {
      // §6.1: a failed sensor sends null with FAULT; a stuck one repeats its value, marked STALE
      if (kind === "SENSOR_OFFLINE") {
        status.readings[probe] = null;
        status.quality[probe] = "FAULT";
      }
      if (kind === "STUCK_READING" && memory) {
        if (memory.stuck === undefined) memory.stuck = status.readings[probe];
        status.readings[probe] = memory.stuck;
        status.quality[probe] = "STALE";
      }
    }
    if (kind === "DEVICE_OFFLINE" && target === zone.controller && memory) {
      // §11.2: still answered, with the last values marked old
      const since = new Date(memory.since).toISOString();
      status.online = false;
      status.lastSeen = since;
      status.ts = since;
      for (const key of Object.keys(status.quality) as ZoneMetric[]) status.quality[key] = "STALE";
    }
  }
  return status;
}
