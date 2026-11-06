import type { ZoneStatus } from "../../api/types";
import { ZONE_READINGS } from "../../config/readings";

/* In-app alerts worked out from GET /api/zones/{zoneId}, which the app polls anyway: no alert API
   yet. Each one says what is wrong and what to do (PRD rule 7), and it goes away by itself once the
   zone stops reporting the condition. */

export type AlertSeverity = "crit" | "warn";

export type AlertKind = "estop" | "leak" | "offline" | "stopped" | "sensor" | "stale" | "reservoir";

export type ZoneAlert = {
  id: string; // `${zoneId}-${kind}`, unique on a page
  zoneId: string;
  zoneName: string;
  kind: AlertKind;
  severity: AlertSeverity;
  title: string; // what is wrong
  text: string; // what the farm did, and what to do
};

/** Below this the reservoir is too low to dose. Display only: the controller and the back end
    refuse the dose themselves. Matches limits.json minReservoirPctToDose (a placeholder, OPEN-5)
    until the zone limits reach the app. */
export const RESERVOIR_LOW_PCT = 30;

type Metric = keyof ZoneStatus["quality"];

const METRIC_NAMES: Record<Metric, string> = {
  ...(Object.fromEntries(ZONE_READINGS.map((spec) => [spec.key, spec.label])) as Record<Metric, string>),
  circFlowLpm: "Circulation flow",
};

/** "EC", "EC and pH", "EC, pH and Solution temp". */
function names(metrics: Metric[]) {
  const list = metrics.map((m) => METRIC_NAMES[m]);
  return list.length < 2 ? list.join("") : `${list.slice(0, -1).join(", ")} and ${list.at(-1)}`;
}

const clock = (iso: string) => new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

const SEVERITY_ORDER: Record<AlertSeverity, number> = { crit: 0, warn: 1 };

/** Critical first; otherwise in the order given (zones in layout order, then the rules below). */
export const bySeverity = (alerts: ZoneAlert[]) =>
  alerts.toSorted((a, b) => SEVERITY_ORDER[a.severity] - SEVERITY_ORDER[b.severity]);

/** One zone's alerts. `unreachable` is true when the app couldn't fetch the zone at all. */
export function zoneAlerts(
  zone: { id: string; name: string },
  t: ZoneStatus | undefined,
  unreachable = false,
): ZoneAlert[] {
  const alerts: ZoneAlert[] = [];
  const add = (kind: AlertKind, severity: AlertSeverity, title: string, text: string) =>
    alerts.push({
      id: `${zone.id}-${kind}`,
      zoneId: zone.id,
      zoneName: zone.name,
      kind,
      severity,
      title,
      text,
    });
  const name = zone.name;

  if (!t) {
    if (unreachable)
      add(
        "offline",
        "crit",
        `Can’t get ${name}’s readings.`,
        "The app keeps trying. Check this device’s internet connection.",
      );
    return alerts;
  }

  if (t.estop)
    add(
      "estop",
      "crit",
      `E-STOP pressed in ${name}.`,
      "All dosing and refill stopped; circulation keeps running. Check the cabinet, release the E-STOP, then press Resume.",
    );
  if (t.leak)
    add("leak", "crit", `Leak sensor wet in ${name}.`, "Check the zone for water and fix any leak.");

  const offline = t.online === false;
  if (offline)
    add(
      "offline",
      "crit",
      `${name} controller is offline.`,
      t.lastSeen
        ? `Last heard at ${clock(t.lastSeen)}. Its readings are old and doses are refused. Check the controller’s power and network cable.`
        : "It has not reported yet. Check the controller is powered and connected.",
    );

  // an E-STOP stops the zone too; its alert already says to press Resume
  if (t.stopped && !t.estop)
    add(
      "stopped",
      "warn",
      `${name} is stopped.`,
      "Nothing restarts by itself, and a dose that was cut short is not started again. Check the zone, then press Resume.",
    );

  // an offline controller's readings are all old: the offline alert says so once
  if (offline) return bySeverity(alerts);

  const quality = Object.entries(t.quality) as [Metric, string][];
  const faulty = quality.filter(([, q]) => q === "FAULT").map(([m]) => m);
  if (faulty.length)
    add(
      "sensor",
      "warn",
      `${names(faulty)} sensor ${faulty.length > 1 ? "faults" : "fault"} in ${name}.`,
      "No value comes from it, and dosing never uses a faulty reading. Check the probe and its cable.",
    );

  // STALE: no fresh sample. A stuck probe shows up the same way (the simulator's STUCK_READING drill).
  const stale = quality.filter(([, q]) => q === "STALE").map(([m]) => m);
  if (stale.length && stale.length === quality.length)
    add(
      "stale",
      "warn",
      `No fresh readings from ${name}.`,
      `${t.ts ? `The last one was at ${clock(t.ts)}. ` : ""}The controller is still connected; check its sensors.`,
    );
  else if (stale.length)
    add(
      "stale",
      "warn",
      `${names(stale)} ${stale.length > 1 ? "readings are" : "reading is"} not updating in ${name}.`,
      "The probe may be stuck, so dosing won’t use it. Check the probe, or recalibrate it.",
    );

  const pct = t.readings.reservoirPct;
  if (pct !== null && t.quality.reservoirPct === "OK" && pct < RESERVOIR_LOW_PCT)
    add(
      "reservoir",
      "warn",
      `${name} reservoir is low (${pct} %).`,
      `Below the ${RESERVOIR_LOW_PCT} % needed to dose, so doses are refused. Refill the reservoir.`,
    );

  return bySeverity(alerts);
}
