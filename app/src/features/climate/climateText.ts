import type { ClimateStatus, Farm, ZoneSource } from "../../api/types";
import { EMPTY_VALUE } from "../../config/readings";

/** A site with no climate controller in its layout shows no climate on its zones. */
export function hasClimateController(farm: Farm) {
  const { controller } = farm.climate;
  return Boolean(controller) && (!farm.devices || farm.devices.some((d) => d.id === controller));
}

/** Who plays the climate controller: layout climate.source (DM-2, §2.4). Left out, as for a
    zone: simulated on a playground site, live everywhere else. client_B's is SIMULATED on a real
    farm whose zones are LIVE. */
export function climateSource(farm: Farm): ZoneSource {
  return farm.climate.source ?? (farm.features.playground ? "SIMULATED" : "LIVE");
}

/** "2 of 4" from equipment.fanStage and the layout's fans. */
export function fanStageText(climate: ClimateStatus, farm: Farm) {
  const stage = climate.equipment.fanStage;
  if (typeof stage !== "number") return EMPTY_VALUE;
  const fans = farm.climate.fans.length;
  return fans ? `${stage} of ${fans}` : String(stage);
}
