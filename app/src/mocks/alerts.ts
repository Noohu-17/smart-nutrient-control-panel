// src/mocks/alerts.ts · the fake GET /api/alerts and POST /api/alerts/{id}/ack (T1-24).
// Alerts are worked out from the fake zone readings (drills and held values included) with the
// limits in data/alerts.json, a copy of config/clients/client_A/alerts.json. No delays, no history.
import type { ServerAlert, ZoneStatus } from "../api/types";
import limits from "./data/alerts.json";
import { farmLayout } from "./data/layout";

type Zones = Record<string, { alarms?: { ecMaxMsCm?: number; solTempMaxC?: number } }>;

const acked = new Map<string, { at: string; by: string | null }>();
const firstSeen = new Map<string, string>();
// The zone as the app last saw it, so asking for alerts doesn't move the fake readings on.
const lastSeen = new Map<string, ZoneStatus>();

export function rememberZone(status: ZoneStatus) {
  lastSeen.set(status.zone, status);
}

export function resetAlerts() {
  acked.clear();
  firstSeen.clear();
  lastSeen.clear();
}

const NO_ACTION = "No automatic action: the farm keeps running as it was";

export function currentAlerts(zoneStatus: (zoneId: string) => ZoneStatus): ServerAlert[] {
  const alerts: ServerAlert[] = [];
  const reservoirLow = limits.events.RESERVOIR_LOW.thresholdPct;
  for (const zone of farmLayout.zones) {
    const t = lastSeen.get(zone.id) ?? zoneStatus(zone.id);
    const alarms = (limits.zones as Zones)[zone.id]?.alarms ?? {};
    const ok = (key: keyof ZoneStatus["quality"]) => (t.quality[key] === "OK" ? t.readings[key] : null);
    const add = (kind: string, message: string, nextStep: string, severity: ServerAlert["severity"] = "WARNING") => {
      const id = `${zone.id}-${kind}`;
      if (!firstSeen.has(id)) firstSeen.set(id, new Date().toISOString());
      const ack = acked.get(id);
      alerts.push({
        id, kind, severity, status: ack ? "ACKNOWLEDGED" : "OPEN", zone: zone.id,
        message, systemAction: NO_ACTION, nextStep, raisedAt: firstSeen.get(id)!, simTs: t.simTs ?? null,
        clearedAt: null, acknowledgedAt: ack?.at ?? null, acknowledgedBy: ack?.by ?? null,
      });
    };
    const ec = ok("ec");
    if (alarms.ecMaxMsCm !== undefined && ec !== null && ec > alarms.ecMaxMsCm)
      add("EC_HIGH", `EC in ${zone.name} is ${ec} mS/cm, above the ${alarms.ecMaxMsCm} alarm limit`,
        "Don't add nutrients; dilute with fresh water and check the EC probe", "CRITICAL");
    const temp = ok("solTempC");
    if (alarms.solTempMaxC !== undefined && temp !== null && temp > alarms.solTempMaxC)
      add("SOL_TEMP_HIGH", `Solution in ${zone.name} is ${temp} °C, above the ${alarms.solTempMaxC} °C alarm limit`,
        "Check the chillers and shading; warm solution holds less oxygen for the roots");
    const level = ok("reservoirPct");
    if (level !== null && level < reservoirLow)
      add("RESERVOIR_LOW", `${zone.name} reservoir is at ${level} %, below ${reservoirLow} %`, "Refill the reservoir");
  }
  for (const id of firstSeen.keys()) if (!alerts.some((a) => a.id === id)) firstSeen.delete(id);
  return alerts;
}

/** Acknowledge as `by` (the signed-in user's name): the alert, or null if it isn't current. A
    Viewer never gets here: the gate answers 403 first (session.ts, §13). */
export function acknowledge(
  zoneStatus: (zoneId: string) => ZoneStatus,
  id: string,
  by: string | null,
): ServerAlert | null {
  if (!currentAlerts(zoneStatus).some((a) => a.id === id)) return null;
  if (!acked.has(id)) acked.set(id, { at: new Date().toISOString(), by });
  return currentAlerts(zoneStatus).find((a) => a.id === id)!;
}
