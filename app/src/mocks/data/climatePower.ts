// src/mocks/data/climatePower.ts · fake CLIMATE_TELEMETRY (contract §6.2) and POWER_STATE (§6.3)
// Air drifts a little on every request, like zone telemetry. Sensor positions, equipment IDs and
// whether a generator or UPS is fitted all come from the layout, never from here.
import type { ClimateTelemetry, Farm, PowerState } from "../../api/types";

type Air = {
  byPos: Record<string, number>;
  airRh: number;
  outsideTempC: number;
  outsideRh: number;
  elecRoomTempC: number;
};

// The contract §6.2 example. A position not listed here starts at the middle reading.
const SEED: Air = {
  byPos: { PAD_END: 29.4, MIDDLE: 31.0, FAN_END: 33.1 },
  airRh: 71,
  outsideTempC: 33.8,
  outsideRh: 58,
  elecRoomTempC: 34.0,
};

let air: Air | null = null;
let climateSeq = 9119;
let powerSeq = 550;

const round = (value: number, decimals: number) => Math.round(value * 10 ** decimals) / 10 ** decimals;
const clamp = (value: number, min: number, max: number) => Math.min(max, Math.max(min, value));
const jitter = (size: number) => (Math.random() * 2 - 1) * size;

function nextAir(positions: string[]): Air {
  if (!air) {
    air = structuredClone(SEED);
    for (const pos of positions) air.byPos[pos] ??= SEED.byPos.MIDDLE;
    return air;
  }
  const byPos: Record<string, number> = {};
  for (const [pos, value] of Object.entries(air.byPos)) byPos[pos] = clamp(value + jitter(0.1), 15, 45);
  air = {
    byPos,
    airRh: clamp(air.airRh + jitter(0.5), 30, 98),
    outsideTempC: clamp(air.outsideTempC + jitter(0.1), 10, 48),
    outsideRh: clamp(air.outsideRh + jitter(0.5), 15, 98),
    elecRoomTempC: clamp(air.elecRoomTempC + jitter(0.1), 15, 50),
  };
  return air;
}

/** Forget the live state (used by tests so each one starts from the seed). */
export function resetClimatePower() {
  air = null;
  climateSeq = 9119;
  powerSeq = 550;
}

export function buildClimateTelemetry(layout: Farm): ClimateTelemetry {
  const { climate } = layout;
  const now = nextAir(climate.airSensors);

  const airTempByPosC: Record<string, number> = {};
  for (const pos of climate.airSensors) airTempByPosC[pos] = round(now.byPos[pos], 1);
  const temps = Object.values(airTempByPosC);
  const airTempC = round(temps.reduce((sum, t) => sum + t, 0) / temps.length, 1); // §6.2: the average

  const equipment: ClimateTelemetry["equipment"] = { fanStage: Math.min(2, climate.fans.length) };
  climate.fans.forEach((fan, i) => (equipment[fan] = i < 2 ? "ON" : "OFF"));
  for (const pad of climate.padPumps) equipment[pad] = "ON";
  for (const shade of climate.shade) equipment[shade] = "OPEN";
  for (const haf of climate.haf) equipment[haf] = "ON";
  for (const chiller of Object.values(climate.chillers))
    equipment[chiller] = { state: "ON", setpointC: 22.0 };
  for (const fog of climate.foggers) equipment[fog] = "OFF";

  climateSeq += 1;
  return {
    v: 1,
    type: "CLIMATE_TELEMETRY",
    msgId: crypto.randomUUID(),
    ts: new Date().toISOString(),
    site: layout.site,
    device: climate.controller,
    bootId: 77,
    seq: climateSeq,
    readings: {
      airTempC,
      airTempByPosC,
      airRh: Math.round(now.airRh),
      outsideTempC: round(now.outsideTempC, 1),
      outsideRh: Math.round(now.outsideRh),
      elecRoomTempC: round(now.elecRoomTempC, 1),
    },
    quality: { airTempC: "OK", airRh: "OK", outsideTempC: "OK", outsideRh: "OK", elecRoomTempC: "OK" },
    mode: "MANUAL",
    manualUntil: null,
    equipment,
    runTimeTodayMin: { FANS: 312, ...Object.fromEntries(climate.padPumps.map((pad) => [pad, 240])) },
    setpoints: { airTempMaxC: 30.0, airRhMax: 85, solTempMaxC: 24.0 },
  };
}

/** §6.3: `generator` and `ups` are left out when the layout says the site has none. */
export function buildPowerState(layout: Farm): PowerState {
  powerSeq += 1;
  const state: PowerState = {
    v: 1,
    type: "POWER_STATE",
    msgId: crypto.randomUUID(),
    ts: new Date().toISOString(),
    site: layout.site,
    device: layout.power.monitor,
    bootId: 3,
    seq: powerSeq,
    mains: "OK",
    phases: { l1V: 231, l2V: 229, l3V: 233 },
    source: "MAINS",
    ats: "MAINS",
  };
  if (layout.power.generator) state.generator = "STOPPED";
  if (layout.power.ups) state.ups = { onBattery: false, batteryPct: 100, runtimeMin: 42 };
  return state;
}
