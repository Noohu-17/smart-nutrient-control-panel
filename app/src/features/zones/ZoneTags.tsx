import { useFarm } from "../../api/hooks";
import type { FarmZone } from "../../api/types";
import { zoneProfile, zoneSource, zoneTags } from "../../config/zoneText";
import type { ZoneStatus } from "./useZoneStatus";

/** What the zone is, at a glance: system, units, crop, stage, crop day, tank, and NOT FITTED for a live zone with no sensors yet (T2-30).
    On the Overview card and above the zone page's readings. */
export function ZoneTags({ zone, status }: { zone: FarmZone; status: ZoneStatus }) {
  const farm = useFarm();
  const tags = zoneTags(zoneProfile(zone, status.t?.profile), status.t?.crop, {
    timezone: farm.data?.timezone ?? "UTC",
    source: zoneSource(zone, status.t?.source, farm.data?.features.playground ?? false),
    now: new Date(),
  });
  return (
    <ul className="zone-tags" aria-label={`About ${zone.name}`}>
      {tags.map((tag, i) => (
        <li key={i}>{tag}</li>
      ))}
    </ul>
  );
}
