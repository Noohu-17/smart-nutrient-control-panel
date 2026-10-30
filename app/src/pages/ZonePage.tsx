import { Link, useParams } from "react-router-dom";
import { useFarm } from "../api/hooks";
import { ReadingTile } from "../components/ReadingTile";
import { EMPTY_VALUE, ZONE_READINGS } from "../config/readings";
import { Placeholder } from "./Placeholder";

/** Zone page skeleton: name and benches from GET /api/farm, reading cards still empty. */
export function ZonePage() {
  const { zoneId } = useParams();
  const farm = useFarm();
  if (farm.isPending) return <Placeholder title="Zone" note="Loading the farm…" />;
  if (farm.isError)
    return <Placeholder title="Zone" note={`Could not load the farm: ${farm.error.message}`} />;

  const zone = farm.data.zones.find((z) => z.id === zoneId);
  if (!zone) return <Placeholder title="Zone not found" note={`There is no zone called "${zoneId}".`} />;

  return (
    <>
      <div className="phead">
        <div>
          <nav className="crumbs" aria-label="Breadcrumb">
            <Link to="/">Overview</Link> / {zone.name}
          </nav>
          <h1>{zone.name}</h1>
        </div>
      </div>

      <section className="zone-card" aria-labelledby="readings-title">
        <header className="zone-card-head">
          <h2 id="readings-title">Readings</h2>
        </header>
        <div className="tiles">
          {ZONE_READINGS.map((spec) => (
            <ReadingTile key={spec.key} label={spec.label} value={EMPTY_VALUE} unit={spec.unit} />
          ))}
        </div>
      </section>

      <section className="zone-card" aria-labelledby="benches-title">
        <header className="zone-card-head">
          <h2 id="benches-title">Benches</h2>
          <span className="chip">{zone.benches.length}</span>
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
  );
}
