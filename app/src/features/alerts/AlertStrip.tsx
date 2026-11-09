import type { ReactNode } from "react";
import type { ZoneAlert } from "./zoneAlerts";

/** Alert strip at the top of a page: one banner per alert, nothing when all is well.
    `action` adds a control to an alert, such as Resume or a link to the zone. */
export function AlertStrip({
  alerts,
  action,
}: {
  alerts: ZoneAlert[];
  action?: (alert: ZoneAlert) => ReactNode;
}) {
  if (alerts.length === 0) return null;
  return (
    <section className="alert-strip" aria-label="Alerts">
      {alerts.map((alert) => (
        <div key={alert.id} className={`banner ${alert.severity}`} role="alert">
          <b>{alert.title}</b> {alert.text} {action?.(alert)}
        </div>
      ))}
    </section>
  );
}
