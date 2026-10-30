import type { Quality, ZoneTelemetry } from "../api/types";

type QualityKey = keyof ZoneTelemetry["quality"];

export type ReadingSpec = { key: QualityKey; label: string; unit: string; decimals: number };

/** Reading cards shown for every zone, in display order. */
export const ZONE_READINGS: ReadingSpec[] = [
  { key: "ec", label: "EC", unit: "mS/cm", decimals: 2 },
  { key: "ph", label: "pH", unit: "", decimals: 2 },
  { key: "solTempC", label: "Solution temp", unit: "°C", decimals: 1 },
  { key: "reservoirPct", label: "Reservoir level", unit: "%", decimals: 0 },
];

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

export const zonePath = (zoneId: string) => `/zone/${zoneId}`;
