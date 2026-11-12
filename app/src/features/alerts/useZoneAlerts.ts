import { useAlerts, useZones } from "../../api/hooks";
import type { FarmZone } from "../../api/types";
import { mergeAlerts } from "./serverAlerts";
import { bySeverity, zoneAlerts } from "./zoneAlerts";

/** The alerts for these zones, critical first: the ones worked out from the zone readings (same
    cache as the zone cards, so no extra calls) and the server's current alerts (T1-24).
    `site` adds alerts that belong to no zone; `live` leaves the server's out. */
export function useZoneAlerts(
  zones: Pick<FarmZone, "id" | "name">[],
  { site = false, live = false }: { site?: boolean; live?: boolean } = {},
) {
  const queries = useZones(zones.map((zone) => zone.id));
  const server = useAlerts();
  const fromReadings = zones.flatMap((zone, i) => zoneAlerts(zone, queries[i].data, queries[i].isError));
  return live ? bySeverity(fromReadings) : mergeAlerts(fromReadings, server.data, zones, site);
}
