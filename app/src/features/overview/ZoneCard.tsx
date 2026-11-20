import { Link } from "react-router-dom";
import type { FarmZone } from "../../api/types";
import { zonePath } from "../../config/readings";
import { ZoneActions } from "../zones/ZoneActions";
import { ZoneReadings, ZoneSourceChip, ZoneStatusChip } from "../zones/ZoneLive";
import { ZoneTags } from "../zones/ZoneTags";
import { useZoneStatus } from "../zones/useZoneStatus";

/** Overview card for one zone: what it is, its latest readings and how fresh they are. Its alerts
    are in the strip at the top of the Overview. */
export function ZoneCard({ zone }: { zone: FarmZone }) {
  const status = useZoneStatus(zone.id);

  return (
    <section className={`zone-card${status.offline ? " offline" : ""}`} aria-labelledby={`zone-${zone.id}`}>
      <header className="zone-card-head">
        <div className="title-row">
          <h2 id={`zone-${zone.id}`}>
            <Link to={zonePath(zone.id)}>{zone.name}</Link>
          </h2>
          <ZoneSourceChip source={zone.source} />
        </div>
        <ZoneStatusChip status={status} />
      </header>
      <ZoneTags zone={zone} status={status} />
      <ZoneReadings status={status} />
      <ZoneActions zoneId={zone.id} />
    </section>
  );
}
