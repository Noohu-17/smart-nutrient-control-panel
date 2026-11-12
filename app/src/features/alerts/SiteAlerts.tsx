import { Link } from "react-router-dom";
import { useFarm } from "../../api/hooks";
import { zonePath } from "../../config/readings";
import { AlertStrip } from "./AlertStrip";
import { useZoneAlerts } from "./useZoneAlerts";

/** Every alert on the pages that have no strip of their own (Climate, History, Simulator …), so an
    alert is seen wherever the grower is. Overview, the zone pages and Alerts show their own. */
export function SiteAlerts() {
  const farm = useFarm();
  const alerts = useZoneAlerts(farm.data?.zones ?? [], { site: true });
  return (
    <AlertStrip
      alerts={alerts}
      action={(alert) =>
        alert.zoneId ? <Link to={zonePath(alert.zoneId)}>Open {alert.zoneName}</Link> : <Link to="/alerts">Alerts</Link>
      }
    />
  );
}
