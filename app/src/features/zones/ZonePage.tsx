import { Link, useParams } from "react-router-dom";
import { useFarm } from "../../api/hooks";
import type { FarmZone } from "../../api/types";
import { EmptyState } from "../../components/EmptyState";
import { StatusChip } from "../../components/StatusChip";
import { AlertStrip } from "../alerts/AlertStrip";
import { useZoneAlerts } from "../alerts/useZoneAlerts";
import { DoseCard } from "../dosing/DoseCard";
import { ZoneActions } from "./ZoneActions";
import { ResumeZone } from "./ResumeZone";
import { ZoneReadings, ZoneStatusChip } from "./ZoneLive";
import { useZoneStatus } from "./useZoneStatus";

/** The zone's alerts, Resume in the stopped or E-STOP one, then the live readings. */
function ZoneLive({ zone }: { zone: FarmZone }) {
  const status = useZoneStatus(zone.id);
  const alerts = useZoneAlerts([zone]);
  return (
    <>
      <AlertStrip
        alerts={alerts}
        action={(alert) =>
          (alert.kind === "stopped" || alert.kind === "estop") && (
            <ResumeZone zoneId={zone.id} status={status} />
          )
        }
      />
      <section className="zone-card" aria-labelledby="readings-title">
        <header className="zone-card-head">
          <h2 id="readings-title">Readings</h2>
          <ZoneStatusChip status={status} />
        </header>
        <ZoneReadings status={status} />
        <ZoneActions full />
      </section>
    </>
  );
}

/** Zone page: name and benches from GET /api/farm, live readings, dosing. */
export function ZonePage() {
  const { zoneId } = useParams();
  const farm = useFarm();
  const zone = farm.data?.zones.find((z) => z.id === zoneId);

  return (
    <>
      <div className="phead">
        <div>
          <nav className="crumbs" aria-label="Breadcrumb">
            <Link to="/">Overview</Link> / {zone?.name ?? zoneId}
          </nav>
          <h1>{zone?.name ?? "Zone"}</h1>
        </div>
      </div>

      {farm.isPending ? (
        <EmptyState title="Loading the farm…" />
      ) : farm.isError ? (
        <EmptyState title="Couldn’t load the farm">{farm.error.message}</EmptyState>
      ) : !zone ? (
        <EmptyState title="Zone not found">There is no zone called "{zoneId}".</EmptyState>
      ) : (
        <>
          <ZoneLive key={`live-${zone.id}`} zone={zone} />

          <DoseCard key={zone.id} zone={zone} />

          <section className="zone-card" aria-labelledby="benches-title">
            <header className="zone-card-head">
              <h2 id="benches-title">Benches</h2>
              <StatusChip>{String(zone.benches.length)}</StatusChip>
            </header>
            <ul className="benches">
              {zone.benches.map((bench) => (
                <li key={bench.id}>
                  <b>{bench.id}</b>
                  <span>
                    {bench.channels} channels · {bench.lengthM} m
                  </span>
                </li>
              ))}
            </ul>
          </section>
        </>
      )}
    </>
  );
}
