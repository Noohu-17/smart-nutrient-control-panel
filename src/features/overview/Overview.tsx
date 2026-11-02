import { useFarm } from "../../api/hooks";
import { EmptyState } from "../../components/EmptyState";
import { ZoneCard } from "./ZoneCard";

/** Home after sign-in. Zone names come from GET /api/farm (the fake API for now, T2-4);
    each zone gets a card with its latest readings. */
export function Overview() {
  const farm = useFarm();
  return (
    <>
      <div className="phead">
        <h1>Overview</h1>
        {farm.data && <p>{farm.data.name}</p>}
      </div>
      {farm.isPending ? (
        <EmptyState title="Loading the farm…" />
      ) : farm.isError ? (
        <EmptyState title="Couldn’t load the farm">{farm.error.message}</EmptyState>
      ) : farm.data.zones.length === 0 ? (
        <EmptyState title="No zones yet">Zones appear here once the farm layout lists them.</EmptyState>
      ) : (
        <div className="zone-grid">
          {farm.data.zones.map((zone) => (
            <ZoneCard key={zone.id} zone={zone} />
          ))}
        </div>
      )}
    </>
  );
}
