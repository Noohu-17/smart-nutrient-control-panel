import { useZones } from "../../api/hooks";
import type { FarmZone } from "../../api/types";
import { bySeverity, zoneAlerts } from "./zoneAlerts";

/** The alerts for these zones, critical first. Same cache as the zone cards, so no extra calls. */
export function useZoneAlerts(zones: Pick<FarmZone, "id" | "name">[]) {
  const queries = useZones(zones.map((zone) => zone.id));
  return bySeverity(zones.flatMap((zone, i) => zoneAlerts(zone, queries[i].data, queries[i].isError)));
}
