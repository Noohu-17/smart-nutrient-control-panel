// src/mocks/data/dosing.ts · what the fake back end knows about dosing in each zone.
// The app gets none of this directly: it only sees it in the POST /api/dose-requests answer (§11.3).
// limits.json is a copy of config/clients/client_A/limits.json. The rest has no config file yet.
import type { DoseLine } from "../../api/types";
import limitsJson from "./limits.json";

export type ZoneDosing = {
  limits: {
    maxPerDoseMl: Record<DoseLine, number>;
    max24hMl: Record<DoseLine, number>;
    ecCeiling: number;
    phMin: number;
    phMax: number;
    minReservoirPctToDose: number;
  };
  mixWaitS: number;
  target: { ecMin: number; ecMax: number; phMin: number; phMax: number };
  dosed24hMl: Record<DoseLine, number>;
  pumpMlPerMin: Record<DoseLine, number>;
  dayTankWarnPct: number;
};

// §11.3 example target and sample use in the last 24 h
const TARGET = { ecMin: 1.5, ecMax: 1.8, phMin: 5.8, phMax: 6.2 };
const DOSED_24H: Record<string, Record<DoseLine, number>> = {
  Z1: { A: 240, B: 240, ACID: 60 },
  Z2: { A: 0, B: 0, ACID: 0 },
};
const PUMP_ML_PER_MIN: Record<DoseLine, number> = { A: 100, B: 100, ACID: 50 };
const DAY_TANK_WARN_PCT = 20;

export function zoneDosing(zoneId: string): ZoneDosing | undefined {
  const zone = (limitsJson.zones as Record<string, (typeof limitsJson.zones)["Z1"]>)[zoneId];
  if (!zone) return undefined;
  return {
    limits: zone.limits,
    mixWaitS: zone.dose.mixWaitS,
    target: TARGET,
    dosed24hMl: DOSED_24H[zoneId] ?? { A: 0, B: 0, ACID: 0 },
    pumpMlPerMin: PUMP_ML_PER_MIN,
    dayTankWarnPct: DAY_TANK_WARN_PCT,
  };
}
