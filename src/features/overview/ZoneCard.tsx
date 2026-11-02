import { Link } from "react-router-dom";
import { useZone } from "../../api/hooks";
import type { FarmZone } from "../../api/types";
import { ReadingCard } from "../../components/ReadingCard";
import { StatusChip } from "../../components/StatusChip";
import { ZONE_READINGS, zonePath } from "../../config/readings";
import { ZoneActions } from "../zones/ZoneActions";

/** Overview card for one zone: its latest readings, or an Offline look when there are none. */
export function ZoneCard({ zone }: { zone: FarmZone }) {
  const telemetry = useZone(zone.id);
  const t = telemetry.data;
  const offline = telemetry.isError;

  return (
    <section className={`zone-card${offline ? " offline" : ""}`} aria-labelledby={`zone-${zone.id}`}>
      <header className="zone-card-head">
        <h2 id={`zone-${zone.id}`}>
          <Link to={zonePath(zone.id)}>{zone.name}</Link>
        </h2>
        {offline ? (
          <StatusChip tone="off">Offline</StatusChip>
        ) : t ? (
          <StatusChip tone="ok">Live</StatusChip>
        ) : (
          <StatusChip>Loading</StatusChip>
        )}
      </header>
      <div className="readings">
        {ZONE_READINGS.map((spec) => (
          <ReadingCard
            key={spec.key}
            spec={spec}
            value={t?.readings[spec.key]}
            quality={t?.quality[spec.key]}
            stale={offline}
          />
        ))}
      </div>
      <ZoneActions />
    </section>
  );
}
