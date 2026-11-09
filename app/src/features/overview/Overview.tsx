import { Link } from "react-router-dom";
import { useFarm } from "../../api/hooks";
import type { FarmZone } from "../../api/types";
import { EmptyState } from "../../components/EmptyState";
import { zonePath } from "../../config/readings";
import { AlertStrip } from "../alerts/AlertStrip";
import { useZoneAlerts } from "../alerts/useZoneAlerts";
import { PowerStrip } from "./PowerStrip";
import { ZoneCard } from "./ZoneCard";

/** Every zone's alerts, critical first, each with a link to its zone (where Resume is). */
function FarmAlerts({ zones }: { zones: FarmZone[] }) {
  return (
    <AlertStrip
      alerts={useZoneAlerts(zones)}
      action={(alert) => <Link to={zonePath(alert.zoneId)}>Open {alert.zoneName}</Link>}
    />
  );
}

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
        <>
          <FarmAlerts zones={farm.data.zones} />
          <PowerStrip layout={farm.data.power} />
          <div className="zone-grid">
            {farm.data.zones.map((zone) => (
              <ZoneCard key={zone.id} zone={zone} />
            ))}
          </div>
        </>
      )}
    </>
  );
}
