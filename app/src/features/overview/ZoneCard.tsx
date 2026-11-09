import { Link } from "react-router-dom";
import type { FarmZone } from "../../api/types";
import { zonePath } from "../../config/readings";
import { ZoneActions } from "../zones/ZoneActions";
import { ZoneReadings, ZoneStatusChip } from "../zones/ZoneLive";
import { useZoneStatus } from "../zones/useZoneStatus";

/** Overview card for one zone: its latest readings and how fresh they are. Its alerts are in the
    strip at the top of the Overview. */
export function ZoneCard({ zone }: { zone: FarmZone }) {
  const status = useZoneStatus(zone.id);

  return (
    <section className={`zone-card${status.offline ? " offline" : ""}`} aria-labelledby={`zone-${zone.id}`}>
      <header className="zone-card-head">
        <h2 id={`zone-${zone.id}`}>
          <Link to={zonePath(zone.id)}>{zone.name}</Link>
        </h2>
        <ZoneStatusChip status={status} />
      </header>
      <ZoneReadings status={status} />
      <ZoneActions />
    </section>
  );
}
