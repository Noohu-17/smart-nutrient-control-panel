// src/mocks/data/telemetry.ts · fake ZONE_TELEMETRY shaped like contract §6.1
// Each request moves the readings a little (sensor noise, plants drinking), so the screens look live.
// Device IDs, capacities, dose lines and equipment IDs all come from the layout, never from here.
import type { DoseLine, FarmZone, ZoneTelemetry } from "../../api/types";
import { farmLayout } from "./layout";

type Chemistry = {
  ec: number;
  ph: number;
  solTempC: number;
  reservoirPct: number;
  circFlowLpm: number;
  dayTankPct: Record<DoseLine, number>;
};

// Starting values per zone. Z1 is the contract §6.1 example; Z2 has a low Stock A day-tank.
// A zone added to the layout without a seed starts from DEFAULT_SEED.
const SEEDS: Record<string, Chemistry> = {
  Z1: {
    ec: 1.62,
    ph: 6.04,
    solTempC: 23.4,
    reservoirPct: 78,
    circFlowLpm: 31.0,
    dayTankPct: { A: 64, B: 66, ACID: 81 },
  },
  Z2: {
    ec: 1.34,
    ph: 6.18,
    solTempC: 24.2,
    reservoirPct: 41,
    circFlowLpm: 29.5,
    dayTankPct: { A: 18, B: 55, ACID: 72 },
  },
};
const DEFAULT_SEED: Chemistry = SEEDS.Z1;

// Live state per zone, kept for as long as the page is open
const state = new Map<string, Chemistry>();
const seqByZone = new Map<string, number>();

const round = (value: number, decimals: number) => Math.round(value * 10 ** decimals) / 10 ** decimals;
const clamp = (value: number, min: number, max: number) => Math.min(max, Math.max(min, value));
const jitter = (size: number) => (Math.random() * 2 - 1) * size;

/** One step of fake physics: small noise plus slow drift, kept inside sane bands. */
function step(previous: Chemistry): Chemistry {
  return {
    ec: clamp(previous.ec + jitter(0.01) - 0.001, 0.8, 2.6), // EC creeps down as plants feed
    ph: clamp(previous.ph + jitter(0.01) + 0.001, 5.2, 7.2), // pH creeps up
    solTempC: clamp(previous.solTempC + jitter(0.05), 18, 30),
    reservoirPct: clamp(previous.reservoirPct - 0.02, 5, 100), // plants drink
    circFlowLpm: clamp(previous.circFlowLpm + jitter(0.3), 25, 35),
    dayTankPct: previous.dayTankPct,
  };
}

function nextReadings(zoneId: string): Chemistry {
  const previous = state.get(zoneId);
  const next = previous ? step(previous) : structuredClone(SEEDS[zoneId] ?? DEFAULT_SEED);
  state.set(zoneId, next);
  return next;
}

/** Forget the live state (used by tests so each one starts from the seeds). */
export function resetTelemetry() {
  state.clear();
  seqByZone.clear();
}

export function findZone(zoneId: string): FarmZone | undefined {
  return farmLayout.zones.find((zone) => zone.id === zoneId);
}

export function buildZoneTelemetry(zoneId: string): ZoneTelemetry | null {
  const zone = findZone(zoneId);
  if (!zone) return null;

  const chem = nextReadings(zoneId);
  const seq = (seqByZone.get(zoneId) ?? 18233) + 1;
  seqByZone.set(zoneId, seq);

  const dayTankPct = {} as Record<DoseLine, number | null>;
  const dayTankL = {} as Record<DoseLine, number | null>;
  const equipment: Record<string, string> = { [`${zoneId}-CIRC`]: "ON", [`${zoneId}-REFILL`]: "CLOSED" };
  for (const line of zone.doseLines) {
    const pct = chem.dayTankPct[line];
    dayTankPct[line] = Math.round(pct);
    dayTankL[line] = round((pct / 100) * zone.dayTanks[line].usableL, 1);
    equipment[`${zoneId}-DOSE-${line}`] = "OFF";
  }

  return {
    v: 1,
    type: "ZONE_TELEMETRY",
    msgId: crypto.randomUUID(),
    ts: new Date().toISOString(), // real time, so "Updated X s ago" and staleness work
    site: farmLayout.site,
    device: zone.controller,
    bootId: 412,
    seq,
    zone: zoneId,
    readings: {
      // precision from contract §1 "Units"
      ec: round(chem.ec, 2),
      ph: round(chem.ph, 2),
      solTempC: round(chem.solTempC, 1),
      reservoirPct: Math.round(chem.reservoirPct),
      reservoirL: Math.round((chem.reservoirPct / 100) * zone.reservoir.usableL),
      circFlowLpm: round(chem.circFlowLpm, 1),
      doMgL: zone.sensors?.dissolvedOxygen ? 7.8 : null, // null when no DO sensor is fitted
      dayTankPct,
      dayTankL,
    },
    quality: { ec: "OK", ph: "OK", solTempC: "OK", reservoirPct: "OK", circFlowLpm: "OK" },
    equipment,
    mode: "REMOTE",
    estop: false,
    leak: false,
    activeCmdId: null,
  };
}
