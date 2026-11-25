import type { ServerAlert } from "../../api/types";
import { bySeverity, type AlertKind, type ZoneAlert } from "./zoneAlerts";

/* Alerts the server raised (GET /api/alerts, T1-24) next to the ones the app works out from the
   zone readings (zoneAlerts.ts). A server alert for something the zone already shows live (an
   E-STOP, a leak, a sensor fault) isn't shown twice. */

const SHOWN_LIVE: Record<string, AlertKind> = {
  ESTOP_PRESSED: "estop",
  LEAK_DETECTED: "leak",
  SENSOR_FAULT: "sensor",
};

/** Not cleared yet: open, or acknowledged while its condition lasts. */
export const isCurrent = (alert: ServerAlert) => alert.status !== "CLEARED";

const sentence = (text: string) => (/[.!?]$/.test(text) ? text : `${text}.`);

/** A server alert in the strip's shape. INFO alerts aren't shown in the strip. */
export function toZoneAlert(alert: ServerAlert, zoneName: string): ZoneAlert {
  return {
    id: `server-${alert.id}`,
    zoneId: alert.zone ?? "",
    zoneName,
    kind: "server",
    severity: alert.severity === "CRITICAL" ? "crit" : "warn",
    title: sentence(alert.message),
    text: `${sentence(alert.systemAction)} ${sentence(alert.nextStep)}`,
  };
}

/** The live alerts and the current server alerts for these zones, critical first. With `site`,
    alerts that belong to no zone (the climate controller's) are included too. */
export function mergeAlerts(
  live: ZoneAlert[],
  server: ServerAlert[] | undefined,
  zones: { id: string; name: string }[],
  site = false,
): ZoneAlert[] {
  const names = new Map(zones.map((zone) => [zone.id, zone.name]));
  const fromServer = (server ?? [])
    .filter((alert) => isCurrent(alert) && alert.severity !== "INFO")
    .filter((alert) => (alert.zone === null ? site : names.has(alert.zone)))
    .filter(
      (alert) => !live.some((shown) => shown.zoneId === alert.zone && shown.kind === SHOWN_LIVE[alert.kind]),
    )
    .map((alert) => toZoneAlert(alert, alert.zone ? (names.get(alert.zone) ?? alert.zone) : ""));
  return bySeverity([...live, ...fromServer]);
}
