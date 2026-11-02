import { Link, useParams } from "react-router-dom";
import { useFarm } from "../api/hooks";
import { EmptyState } from "../components/EmptyState";
import { ReadingCard } from "../components/ReadingCard";
import { StatusChip } from "../components/StatusChip";
import { ZONE_READINGS } from "../config/readings";
import { ZoneActions } from "../features/zones/ZoneActions";

/** Zone page skeleton: name and benches from GET /api/farm, reading cards still empty. */
export function ZonePage() {
  const { zoneId } = useParams();
  const farm = useFarm();
  const zone = farm.data?.zones.find((z) => z.id === zoneId);

  return (
    <>
      <div className="phead">
        <div>
          <nav className="crumbs" aria-label="Breadcrumb">
            <Link to="/">Overview</Link> / {zone?.name ?? zoneId}
          </nav>
          <h1>{zone?.name ?? "Zone"}</h1>
        </div>
      </div>

      {farm.isPending ? (
        <EmptyState title="Loading the farm…" />
      ) : farm.isError ? (
        <EmptyState title="Couldn’t load the farm">{farm.error.message}</EmptyState>
      ) : !zone ? (
        <EmptyState title="Zone not found">There is no zone called "{zoneId}".</EmptyState>
      ) : (
        <>
          <section className="zone-card" aria-labelledby="readings-title">
            <header className="zone-card-head">
              <h2 id="readings-title">Readings</h2>
            </header>
            <div className="readings">
              {ZONE_READINGS.map((spec) => (
                <ReadingCard key={spec.key} spec={spec} value={null} />
              ))}
            </div>
            <ZoneActions />
          </section>

          <section className="zone-card" aria-labelledby="benches-title">
            <header className="zone-card-head">
              <h2 id="benches-title">Benches</h2>
              <StatusChip>{String(zone.benches.length)}</StatusChip>
            </header>
            <ul className="benches">
              {zone.benches.map((bench) => (
                <li key={bench.id}>
                  <b>{bench.id}</b>
                  <span>
                    {bench.channels} channels · {bench.lengthM} m
                  </span>
                </li>
              ))}
            </ul>
          </section>
        </>
      )}
    </>
  );
}
