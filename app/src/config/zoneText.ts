// src/config/zoneText.ts · T2-30 · how a zone, its crop and its nutrients read on screen.
// Display text only: every value comes from the config pack through the API (contract §2.4,
// §11.2, T1-38), so a new farm needs no change here. An ID this file doesn't know shows as itself.
import type {
  DoseLineLimit,
  FarmZone,
  GrowingUnit,
  Tank,
  ZoneCrop,
  ZoneProfile,
  ZoneSource,
} from "../api/types";
import { lineName } from "./dosing";

const SYSTEM_NAMES: Record<string, string> = {
  NFT: "NFT",
  DWC: "Deep water",
  DUTCH_BUCKET: "Dutch bucket",
  DRIP: "Drip",
  VERTICAL: "Tower",
  MIXED: "Mixed",
};

/** [one, many] for each growing unit type. */
const UNIT_WORDS: Record<string, [string, string]> = {
  NFT_BENCH: ["bench", "benches"],
  DWC_RAFT: ["raft", "rafts"],
  DUTCH_BUCKET: ["bucket", "buckets"],
  GROW_BAG: ["grow bag", "grow bags"],
  VERTICAL_TOWER: ["tower", "towers"],
};

const SENSOR_NAMES: Record<string, string> = {
  ec: "EC",
  ph: "pH",
  solTemp: "Solution temp",
  reservoirLevel: "Tank level",
  circFlow: "Circulation flow",
  dayTankLevel: "Day-tank level",
  leak: "Leak",
  dissolvedOxygen: "Dissolved oxygen",
};

const LINK_NAMES: Record<string, string> = {
  MQTT: "Farm network (MQTT)",
  SF_GATEWAY: "Prototype rig gateway",
};

const DAY_MS = 86_400_000;

/** SEEDLING → Seedling, DUTCH_BUCKET → Dutch bucket. */
export const titleCase = (id: string) => {
  const words = id.toLowerCase().replaceAll("_", " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
};

export const systemName = (system: string) => SYSTEM_NAMES[system] ?? titleCase(system);
export const sensorName = (sensor: string) => SENSOR_NAMES[sensor] ?? sensor;
export const linkName = (link: string) => LINK_NAMES[link] ?? link;

const plural = (n: number, [one, many]: [string, string]) => `${n} ${n === 1 ? one : many}`;
const unitWords = (type: string): [string, string] => {
  const known = UNIT_WORDS[type];
  if (known) return known;
  const word = titleCase(type).toLowerCase();
  return [word, `${word}s`];
};

/** "4 benches" */
export const unitCount = (unit: GrowingUnit) => plural(unit.count, unitWords(unit.type));

/** "4 benches · 3 channels × 2.1 m · 45 plant sites each" */
export function unitDetail(unit: GrowingUnit) {
  const size = [
    unit.channels !== undefined && plural(unit.channels, ["channel", "channels"]),
    unit.lengthM !== undefined && `${unit.lengthM} m`,
    unit.widthM !== undefined && `${unit.widthM} m wide`,
    unit.volumeL !== undefined && `${unit.volumeL} L`,
    unit.heightM !== undefined && `${unit.heightM} m tall`,
  ].filter(Boolean);
  return [unitCount(unit), size.join(" × "), `${unit.plantSitesEach} plant sites each`]
    .filter(Boolean)
    .join(" · ");
}

/** The tank's full size, or what can be drawn from it when the pack gives no full size. */
export const tankLitres = (tank: Tank) => tank.volumeL ?? tank.usableL;

/** "10 L (9 L usable)" */
export const tankDetail = (tank: Tank) =>
  tank.volumeL !== undefined && tank.volumeL !== tank.usableL
    ? `${tank.volumeL} L (${tank.usableL} L usable)`
    : `${tank.usableL} L`;

export const cropName = (crop: ZoneCrop) => crop.name ?? titleCase(crop.crop);

/** The farm's own calendar day today, YYYY-MM-DD, in its time zone (layout.json timezone). */
export function farmToday(timezone: string, now: Date) {
  // en-CA writes dates as YYYY-MM-DD
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: timezone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(now);
}

/** Which day of the crop today is: the planting day is day 1. null with no planting day, or one
    still to come. */
export function cropDay(plantedOn: string | null, timezone: string, now: Date) {
  if (!plantedOn) return null;
  const days = (Date.parse(farmToday(timezone, now)) - Date.parse(plantedOn)) / DAY_MS;
  return Number.isFinite(days) && days >= 0 ? Math.round(days) + 1 : null;
}

/** "day 18 of 35", or "day 18" while days to harvest aren't in the config pack. */
export function cropDayText(crop: ZoneCrop, timezone: string, now: Date) {
  const day = cropDay(crop.plantedOn, timezone, now);
  if (day === null) return null;
  return crop.daysToHarvest ? `day ${day} of ${crop.daysToHarvest}` : `day ${day}`;
}

/** What the zone is: the API's profile, or the same fields from GET /api/farm before it arrives. */
export function zoneProfile(zone: FarmZone, profile?: ZoneProfile): ZoneProfile {
  if (profile) return profile;
  const units = zone.growingUnits ?? [];
  return {
    name: zone.name,
    growingSystem: zone.growingSystem ?? null,
    growingUnits: units,
    plantSites: units.length ? units.reduce((sum, u) => sum + u.count * u.plantSitesEach, 0) : null,
    size: zone.size ?? null,
    position: zone.position ?? null,
    tank: zone.reservoir,
    sensors: zone.sensors ?? {},
    controller: zone.controller,
    link: "MQTT",
    doseLines: zone.doseLines,
    notes: zone.notes ?? null,
  };
}

/** The zone's source: the zone answer's, else the layout's, else what GET /api/farm defaults it to
    (SIMULATED on a playground site, LIVE everywhere else, §2.4). */
export const zoneSource = (zone: FarmZone, answer: ZoneSource | undefined, playground: boolean): ZoneSource =>
  answer ?? zone.source ?? (playground ? "SIMULATED" : "LIVE");

/** "NOT FITTED" for a live zone with no sensors fitted yet. Live / Simulated itself is T2-26's chip
    next to the zone name, so it is not repeated here. */
export function fittedTag(profile: ZoneProfile, source: ZoneSource) {
  if (source === "SIMULATED") return null;
  const sensors = Object.values(profile.sensors);
  return sensors.length > 0 && sensors.every((fitted) => !fitted) ? "NOT FITTED" : null;
}

/** The zone card's tags, e.g. "NFT · 4 benches · Lettuce · Seedling · day 9 · 10 L tank". */
export function zoneTags(
  profile: ZoneProfile,
  crop: ZoneCrop | null | undefined,
  { timezone, source, now }: { timezone: string; source: ZoneSource; now: Date },
) {
  return [
    profile.growingSystem && systemName(profile.growingSystem),
    profile.growingUnits.map(unitCount).join(", "),
    crop && cropName(crop),
    crop && titleCase(crop.stage),
    crop && cropDayText(crop, timezone, now),
    `${tankLitres(profile.tank)} L tank`,
    fittedTag(profile, source),
  ].filter((tag): tag is string => Boolean(tag));
}

/** The short line under a dose amount: "Grow: nitrogen and calcium · City Greens Grow 24-00-00, N 24% · …".
    The name is left out when it only repeats the line's label, e.g. client_A's "Stock A". */
export function nutrientText(info: DoseLineLimit | undefined) {
  if (!info) return null;
  const name = info.name && info.name !== lineName(info.line) ? info.name : null;
  const compound =
    info.compound && info.strengthPct != null ? `${info.compound} ${info.strengthPct}%` : info.compound;
  const parts = [name, compound, info.purpose].filter(Boolean);
  return parts.length ? parts.join(" · ") : null;
}

/** "Planted 10 Nov 2026" in the farm's own calendar. */
export function plantedText(plantedOn: string) {
  const day = new Date(`${plantedOn}T00:00:00Z`);
  if (Number.isNaN(day.getTime())) return plantedOn;
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: "UTC",
    day: "numeric",
    month: "short",
    year: "numeric",
  }).format(day);
}
