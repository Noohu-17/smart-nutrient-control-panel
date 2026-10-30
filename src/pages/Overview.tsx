import { Link } from "react-router-dom";
import { useFarm } from "../api/hooks";

/** Home after sign-in. Zone names come from GET /api/farm (the fake API for now, T2-4);
    zone cards with readings come later. */
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
        <ul aria-label="Zones">
          {farm.data.zones.map((zone) => (
            <li key={zone.id}>
              <h2>
                <Link to={`/zone/${zone.id}`}>{zone.name}</Link>
              </h2>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
