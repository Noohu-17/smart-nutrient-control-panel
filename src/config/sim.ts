import type { Farm, FailureDrill, ScenarioOptions, SimRange, SimSpeed } from "../api/types";
import { lineName } from "./dosing";

/* Playground controls (contract §11.4). The drills and the holdable ranges come from
   GET /api/sim/scenario-options (scenarios.json) and the targets from GET /api/farm, so a
   change to either config needs no code. Only display text and the §11.4 speeds live here. */

/** POST /sim/speed accepts exactly these (§11.4). */
export const SPEEDS: SimSpeed[] = [0, 1, 60, 288];

export const speedLabel = (x: number) => (x === 0 ? "Paused" : `×${x}`);

/** Target of a held value on the polyhouse air, and of a POLYHOUSE drill. */
export const POLYHOUSE = "POLYHOUSE";

const METRICS: Record<string, { label: string; unit: string }> = {
  ec: { label: "EC", unit: "mS/cm" },
  ph: { label: "pH", unit: "" },
  solTempC: { label: "Solution temp", unit: "°C" },
  circFlowLpm: { label: "Circulation flow", unit: "L/min" },
  reservoirPct: { label: "Reservoir level", unit: "%" },
  doMgL: { label: "Dissolved oxygen", unit: "mg/L" },
  airTempC: { label: "Air temp", unit: "°C" },
  airRh: { label: "Humidity", unit: "%" },
};

/** "EC", "Day-tank Stock A"; a metric added to scenarios.json later shows as its key. */
export function metricLabel(metric: string) {
  const [base, line] = metric.split(".");
  if (base === "dayTankPct" && line) return `Day-tank ${lineName(line)}`;
  return METRICS[metric]?.label ?? metric;
}

export const metricUnit = (metric: string) =>
  metric.startsWith("dayTankPct.") ? "%" : (METRICS[metric]?.unit ?? "");

/** "Zone A", "Zone A · pH", "Zone A · Stock A", "Polyhouse"; a device as its ID. */
export function targetLabel(farm: Farm, target: string) {
  if (target === POLYHOUSE) return "Polyhouse";
  const [zoneId, part] = target.split(".");
  const zone = farm.zones.find((z) => z.id === zoneId);
  if (!zone) return target;
  if (!part) return zone.name;
  return `${zone.name} · ${part in METRICS ? metricLabel(part) : lineName(part)}`;
}

/** Zone sensors a drill can fake (§11.4.2: "to fake only the sensor, use a failure drill"). */
const ZONE_PROBES = ["ec", "ph", "solTempC"];

/** What a drill can be aimed at on this farm. Zone parts are "Z1.ph" and "Z1.ACID". */
export function drillTargets(farm: Farm, drill: Pick<FailureDrill, "targetType">): string[] {
  switch (drill.targetType) {
    case "ZONE":
      return farm.zones.map((z) => z.id);
    case "ZONE_SENSOR":
      return farm.zones.flatMap((z) =>
        [...ZONE_PROBES, ...(z.sensors?.dissolvedOxygen ? ["doMgL"] : [])].map((s) => `${z.id}.${s}`),
      );
    case "DOSE_LINE":
      return farm.zones.flatMap((z) => z.doseLines.map((l) => `${z.id}.${l}`));
    case "POLYHOUSE":
      return [POLYHOUSE];
    case "DEVICE":
      return (farm.devices ?? []).map((d) => d.id);
    default:
      return [];
  }
}

/** Where a value can be held: each zone, then the polyhouse air. */
export const holdTargets = (farm: Farm) => [...farm.zones.map((z) => z.id), POLYHOUSE];

/** The metrics a target can hold, with their allowed range. A `linesFrom` entry (day-tanks)
    becomes one metric per dosing line the zone has, e.g. "dayTankPct.ACID". */
export function holdMetrics(
  farm: Farm,
  holdable: ScenarioOptions["holdable"],
  target: string,
): { metric: string; range: SimRange }[] {
  if (target === POLYHOUSE)
    return Object.entries(holdable.polyhouse).map(([metric, range]) => ({ metric, range }));
  const zone = farm.zones.find((z) => z.id === target);
  if (!zone) return [];
  return Object.entries(holdable.zone).flatMap(([metric, { min, max, linesFrom }]) =>
    linesFrom
      ? zone.doseLines.map((line) => ({ metric: `${metric}.${line}`, range: { min, max } }))
      : [{ metric, range: { min, max } }],
  );
}
