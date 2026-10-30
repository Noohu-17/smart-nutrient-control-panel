import { Link } from "react-router-dom";
import { useZone } from "../../api/hooks";
import type { FarmZone } from "../../api/types";
import { ReadingTile } from "../../components/ReadingTile";
import { EMPTY_VALUE, formatReading, QUALITY_TEXT, ZONE_READINGS, zonePath } from "../../config/readings";

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
          <span className="chip chip-offline">Offline</span>
        ) : t ? (
          <span className="chip chip-live">Live</span>
        ) : (
          <span className="chip">Loading</span>
        )}
      </header>
      <div className="tiles">
        {ZONE_READINGS.map((spec) => {
          if (!t) {
            return (
              <ReadingTile
                key={spec.key}
                label={spec.label}
                value={EMPTY_VALUE}
                unit={spec.unit}
                note={offline ? "No data" : undefined}
              />
            );
          }
          const value = t.readings[spec.key];
          const quality = t.quality[spec.key];
          const reason =
            quality && quality !== "OK" ? QUALITY_TEXT[quality] : value === null ? "No data" : undefined;
          return (
            <ReadingTile
              key={spec.key}
              label={spec.label}
              value={formatReading(value, spec)}
              unit={spec.unit}
              note={reason}
              warn={reason !== undefined}
            />
          );
        })}
      </div>
    </section>
  );
}
