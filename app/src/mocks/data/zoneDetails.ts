// src/mocks/data/zoneDetails.ts · T2-30 · what the fake back end says a zone, crop and dose line are,
// built as the real one builds them (backend/app/api/pack.py, T1-38).
// recipes.json and nutrients.json are copies of config/clients/client_A's; handlers.test.ts fails if
// they drift apart. client_A's io_map.json puts no controller on the rig, so every link is MQTT.
import type { DoseLimits, FarmZone, ZoneCrop } from "../../api/types";
import { zoneProfile } from "../../config/zoneText";
import type { ZoneDosing } from "./dosing";
import nutrientsJson from "./nutrients.json";
import recipesJson from "./recipes.json";

type Stock = {
  name?: string;
  compound?: string;
  strengthPct?: number;
  drum?: { id: string; volumeL: number };
  purpose?: string;
};
type Applied = { crop: string; variety?: string; stage: string; plantedOn?: string };

const STOCK = nutrientsJson.doseLines as Record<string, Stock>;
const APPLIED = recipesJson.zones as Record<string, Applied>;

/** GET /api/zones/{zoneId} `profile` and `crop` (contract §11.2). */
export function zoneDetails(zone: FarmZone) {
  const applied = APPLIED[zone.id];
  const crop: ZoneCrop | null = applied
    ? {
        crop: applied.crop,
        name: recipesJson.crops.find((c) => c.id === applied.crop)?.name ?? null,
        variety: applied.variety ?? null,
        stage: applied.stage,
        plantedOn: applied.plantedOn ?? null,
      }
    : null;
  return { profile: zoneProfile(zone), crop };
}

/** GET /api/zones/{zoneId}/dose-limits (contract §11.2): the limits the fake review checks, and
    what each line doses. */
export function doseLimits(zone: FarmZone, dosing: ZoneDosing): DoseLimits {
  return {
    zone: zone.id,
    lines: zone.doseLines.map((line) => {
      const stock = STOCK[line] ?? {};
      const max24hMl = dosing.limits.max24hMl[line] ?? 0;
      const dosed24hMl = dosing.dosed24hMl[line] ?? 0;
      return {
        line,
        name: stock.name ?? null,
        compound: stock.compound ?? null,
        strengthPct: stock.strengthPct ?? null,
        drum: stock.drum ?? null,
        purpose: stock.purpose ?? null,
        pumpMlPerMin: dosing.pumpMlPerMin[line] ?? null,
        maxPerDoseMl: dosing.limits.maxPerDoseMl[line] ?? 0,
        max24hMl,
        dosed24hMl,
        left24hMl: Math.max(0, max24hMl - dosed24hMl),
      };
    }),
  };
}
