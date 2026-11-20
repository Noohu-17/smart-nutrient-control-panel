import { useAckAlert, useAlerts, useFarm } from "../../api/hooks";
import type { ServerAlert } from "../../api/types";
import { EmptyState } from "../../components/EmptyState";
import { RoleButton } from "../../components/RoleButton";
import { StatusChip } from "../../components/StatusChip";
import { AlertStrip } from "./AlertStrip";
import { isCurrent } from "./serverAlerts";
import { useZoneAlerts } from "./useZoneAlerts";

const CLEARED_SHOWN = 20;
const SEVERITY_TEXT = { CRITICAL: "Critical", WARNING: "Warning", INFO: "Info" } as const;
const SEVERITY_TONE = { CRITICAL: "crit", WARNING: "warn", INFO: "neutral" } as const;

const clock = (iso: string) =>
  new Date(iso).toLocaleString([], { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });

/** "Raised 9 Nov, 10:42 (farm clock 9 Nov, 19:30)" on a playground site. A live zone runs on
    real time (§2.4), so its alerts show the real time only. */
function when(alert: ServerAlert, live: boolean) {
  const farm = alert.simTs && !live ? ` (farm clock ${clock(alert.simTs)})` : "";
  return `Raised ${clock(alert.raisedAt)}${farm}`;
}

function AlertItem({ alert, zoneName, live }: { alert: ServerAlert; zoneName: string; live: boolean }) {
  const ack = useAckAlert();
  return (
    <li>
      <div>
        <b>
          {zoneName && `${zoneName} · `}
          {alert.message}
        </b>
        <span>
          {alert.systemAction}. {alert.nextStep}.
        </span>
        <small>
          {when(alert, live)}
          {alert.acknowledgedAt &&
            ` · Acknowledged${alert.acknowledgedBy ? ` by ${alert.acknowledgedBy}` : ""} at ${clock(alert.acknowledgedAt)}`}
          {alert.clearedAt && ` · Cleared ${clock(alert.clearedAt)}`}
        </small>
        {ack.isError && <small role="alert">Not acknowledged: {ack.error.message}</small>}
      </div>
      <StatusChip tone={SEVERITY_TONE[alert.severity]}>{SEVERITY_TEXT[alert.severity]}</StatusChip>
      {alert.status === "OPEN" && (
        <RoleButton perm="ack" onClick={() => ack.mutate(alert.id)}>
          {ack.isPending ? "Acknowledging…" : "Acknowledge"}
        </RoleButton>
      )}
    </li>
  );
}

/** Alerts: what needs attention now (the live zone alerts and the server's current alerts, each
    to acknowledge), then what has cleared lately. GET /api/alerts (T1-24). */
export function Alerts() {
  const farm = useFarm();
  const zones = farm.data?.zones ?? [];
  const names = new Map(zones.map((zone) => [zone.id, zone.name]));
  const live = useZoneAlerts(zones, { live: true });
  const server = useAlerts();
  const ack = useAckAlert();
  const all = server.data ?? [];
  const current = all.filter(isCurrent);
  const cleared = all.filter((alert) => !isCurrent(alert)).slice(0, CLEARED_SHOWN);
  const open = current.filter((alert) => alert.status === "OPEN");
  const zoneName = (alert: ServerAlert) => (alert.zone ? (names.get(alert.zone) ?? alert.zone) : "");
  const liveZone = (alert: ServerAlert) => zones.some((z) => z.id === alert.zone && z.source === "LIVE");

  return (
    <>
      <div className="phead">
        <h1>Alerts</h1>
        <div className="actions">
          <RoleButton perm="ack" onClick={() => open.forEach((alert) => ack.mutate(alert.id))}>
            Acknowledge all
          </RoleButton>
        </div>
      </div>

      <AlertStrip alerts={live} />

      {server.isError ? (
        <EmptyState title="Couldn’t load the alerts">{server.error.message}</EmptyState>
      ) : server.isPending ? (
        <EmptyState title="Loading alerts…" />
      ) : current.length === 0 && live.length === 0 ? (
        <EmptyState title="No open alerts">Nothing needs attention right now.</EmptyState>
      ) : current.length > 0 ? (
        <section className="zone-card" aria-labelledby="alerts-now">
          <header className="zone-card-head">
            <h2 id="alerts-now">Needs attention</h2>
            <StatusChip tone={open.length ? "warn" : "neutral"}>{`${open.length} to acknowledge`}</StatusChip>
          </header>
          <ul className="sim-list" aria-label="Current alerts">
            {current.map((alert) => (
              <AlertItem key={alert.id} alert={alert} zoneName={zoneName(alert)} live={liveZone(alert)} />
            ))}
          </ul>
        </section>
      ) : null}

      {cleared.length > 0 && (
        <section className="zone-card" aria-labelledby="alerts-cleared">
          <header className="zone-card-head">
            <h2 id="alerts-cleared">Cleared</h2>
          </header>
          <ul className="sim-list" aria-label="Cleared alerts">
            {cleared.map((alert) => (
              <AlertItem key={alert.id} alert={alert} zoneName={zoneName(alert)} live={liveZone(alert)} />
            ))}
          </ul>
        </section>
      )}
    </>
  );
}
