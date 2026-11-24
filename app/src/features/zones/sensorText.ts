// src/features/zones/sensorText.ts · DM-3 · which sensors a zone shows, fitted or not, and their live
// values. Every sensor and where its value is comes from sensors.json (GET /api/sensors), never code.
import type {
  ClimateStatus,
  Farm,
  FarmZone,
  SensorCatalogue,
  SensorInfo,
  SensorReading,
  ZoneSource,
  ZoneStatus,
} from "../../api/types";
import { EMPTY_VALUE } from "../../config/readings";

/** The zone's farm type and its sensors, from its growingSystem; null if the catalogue has none. */
export function zoneFarmType(catalogue: SensorCatalogue, zone: FarmZone) {
  const type = zone.growingSystem ? catalogue.growingSystems[zone.growingSystem] : undefined;
  return type ? (catalogue.farmTypes[type] ?? null) : null;
}

/** Fitted for the whole house when the site has a climate controller, otherwise when the zone's
    layout sensors say so. A sensor with neither can't be fitted yet. */
export function isFitted(sensor: SensorInfo, zone: FarmZone, farm: Farm) {
  if (sensor.fittedBy === "CLIMATE_CONTROLLER") return Boolean(farm.climate.controller);
  return Boolean(sensor.layoutSensor) && zone.sensors?.[sensor.layoutSensor!] === true;
}

/** Who plays the climate controller: layout climate.source, left out as for a zone (§2.4). */
export function climateSource(farm: Farm): ZoneSource {
  return farm.climate.source ?? (farm.features.playground ? "SIMULATED" : "LIVE");
}

/** Live or Simulated, for a fitted sensor: the climate controller's for the air, else the zone's. */
export function sensorSource(sensor: SensorInfo, zone: FarmZone, farm: Farm): ZoneSource {
  return sensor.fittedBy === "CLIMATE_CONTROLLER" ? climateSource(farm) : zone.source;
}

/** Does any of these sensors read from GET /api/climate? */
export const readsClimate = (sensors: SensorInfo[]) =>
  sensors.some((s) => s.readings.some((r) => r.from === "climate.readings"));

/** The reading's value as the zone or climate answer has it; undefined until that answer comes. */
export function readingValue(reading: SensorReading, zone?: ZoneStatus, climate?: ClimateStatus) {
  const source =
    reading.from === "climate.readings" ? climate?.readings : reading.from === "zone" ? zone : zone?.readings;
  if (!source) return undefined;
  const value = (source as Record<string, unknown>)[reading.key];
  return typeof value === "number" || typeof value === "boolean" ? value : null;
}

/** "6.05", "Dry", or "--" for no value. */
export function formatSensorValue(reading: SensorReading, value: number | boolean | null | undefined) {
  if (typeof value === "boolean") return reading.states?.[value ? "true" : "false"] ?? (value ? "Yes" : "No");
  return typeof value === "number" ? value.toFixed(reading.decimals ?? 1) : EMPTY_VALUE;
}

export const NECESSITY_TEXT: Record<SensorInfo["necessity"], string> = {
  MUST_HAVE: "Must have",
  GOOD_TO_HAVE: "Good to have",
};
