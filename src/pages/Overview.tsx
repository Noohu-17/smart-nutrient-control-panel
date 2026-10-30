import { useFarm } from "../api/hooks";
import { ZoneCard } from "../features/overview/ZoneCard";

/** Home after sign-in. Zone names come from GET /api/farm (the fake API for now, T2-4);
    each zone gets a card with its latest readings. */
export function Overview() {
  const farm = useFarm();

  let note = "Loading the farm…";
  if (farm.isError) note = `Couldn’t load the farm: ${farm.error.message}`;
  else if (farm.data) note = farm.data.name;

  return (
    <>
      <div className="phead">
        <h1>Overview</h1>
        <p>{note}</p>
      </div>
      {farm.data && (
        <div className="zone-grid">
          {farm.data.zones.map((zone) => (
            <ZoneCard key={zone.id} zone={zone} />
          ))}
        </div>
      )}
    </>
  );
}
