import type { ClimateTelemetry, Quality, ZoneStatus } from "../api/types";

type ZoneKey = keyof ZoneStatus["quality"];
type ClimateKey = keyof ClimateTelemetry["quality"];

export type ReadingSpec<K extends string = string> = {
  key: K;
  label: string;
  unit: string;
  decimals: number;
};

/** Reading cards shown for every zone, in display order. */
export const ZONE_READINGS: ReadingSpec<ZoneKey>[] = [
  { key: "ec", label: "EC", unit: "mS/cm", decimals: 2 },
  { key: "ph", label: "pH", unit: "", decimals: 2 },
  { key: "solTempC", label: "Solution temp", unit: "°C", decimals: 1 },
  { key: "reservoirPct", label: "Reservoir level", unit: "%", decimals: 0 },
];

/** Climate page: air inside, then outside, in display order (contract §6.2). */
export const AIR_READINGS: ReadingSpec<ClimateKey>[] = [
  { key: "airTempC", label: "Air temp", unit: "°C", decimals: 1 },
  { key: "airRh", label: "Humidity", unit: "%", decimals: 0 },
  { key: "outsideTempC", label: "Outside temp", unit: "°C", decimals: 1 },
  { key: "outsideRh", label: "Outside humidity", unit: "%", decimals: 0 },
];

export const ELEC_ROOM_READING: ReadingSpec<ClimateKey> = {
  key: "elecRoomTempC",
  label: "Electrical room",
  unit: "°C",
  decimals: 1,
};

/** One card per air sensor position; positions come from layout.climate.airSensors. */
export const AIR_POSITION_LABEL: Record<string, string> = {
  PAD_END: "Pad end",
  MIDDLE: "Middle",
  FAN_END: "Fan end",
};

export const airPositionSpec = (position: string): ReadingSpec => ({
  key: position,
  label: AIR_POSITION_LABEL[position] ?? position,
  unit: "°C",
  decimals: 1,
});

/** What each §6.1 quality code means to a grower. */
export const QUALITY_TEXT: Record<Quality, string> = {
  OK: "OK",
  STALE: "No fresh sample",
  OUT_OF_RANGE: "Outside sensor range",
  FAULT: "Sensor fault",
  UNCALIBRATED: "Not calibrated",
};

export const EMPTY_VALUE = "--";

export function formatReading(value: number | null | undefined, spec: ReadingSpec) {
  return value === null || value === undefined ? EMPTY_VALUE : value.toFixed(spec.decimals);
}

/** "30 s ago", "5 min ago". */
export function formatAge(seconds: number) {
  return seconds < 60 ? `${seconds} s ago` : `${Math.floor(seconds / 60)} min ago`;
}

export const zonePath = (zoneId: string) => `/zone/${zoneId}`;
