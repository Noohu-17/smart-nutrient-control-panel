import { useEffect } from "react";
import { Link, useLocation, useParams } from "react-router-dom";
import { useFarm } from "../../api/hooks";
import type { Farm, FarmZone } from "../../api/types";
import { zonePath } from "../../config/readings";
import { EmptyState } from "../../components/EmptyState";
import { StatusChip } from "../../components/StatusChip";
import { AlertStrip } from "../alerts/AlertStrip";
import { hasClimateController } from "../climate/climateText";
import { ZoneClimateCard } from "../climate/ZoneClimate";
import { useZoneAlerts } from "../alerts/useZoneAlerts";
import { DoseCard } from "../dosing/DoseCard";
import { ZoneTags } from "./ZoneTags";
import { DOSE_CARD, goToDoseCard } from "./doseCard";
import { ZoneActions } from "./ZoneActions";
import { ZoneDetails } from "./ZoneDetails";
import { ZoneSensorReadings } from "./ZoneSensors";
import { ResumeZone } from "./ResumeZone";
import { ZoneSourceChip, ZoneStatusChip } from "./ZoneLive";
import { useZoneStatus } from "./useZoneStatus";

/** Every alert, as on any other page (Resume in this zone's stopped or E-STOP one, a link to the
    zone for another zone's), then the live readings. */
function ZoneLive({ zone, farm }: { zone: FarmZone; farm: Farm }) {
  const zones = farm.zones;
  const status = useZoneStatus(zone.id);
  const alerts = useZoneAlerts(zones, { site: true });
  return (
    <>
      <AlertStrip
        alerts={alerts}
        action={(alert) =>
          alert.zoneId !== zone.id ? (
            alert.zoneId ? (
              <Link to={zonePath(alert.zoneId)}>Open {alert.zoneName}</Link>
            ) : (
              <Link to="/alerts">Alerts</Link>
            )
          ) : (
            (alert.kind === "stopped" || alert.kind === "estop") && (
              <ResumeZone zoneId={zone.id} status={status} />
            )
          )
        }
      />
      <section className="zone-card" aria-labelledby="readings-title">
        <header className="zone-card-head">
          <h2 id="readings-title">Readings</h2>
          <ZoneStatusChip status={status} />
        </header>
        <ZoneTags zone={zone} status={status} />
        <ZoneSensorReadings zone={zone} farm={farm} status={status} />
        <ZoneActions zoneId={zone.id} full />
      </section>
    </>
  );
}

/** Zone page: name and benches from GET /api/farm, live readings, dosing. */
export function ZonePage() {
  const { zoneId } = useParams();
  const farm = useFarm();
  const zone = farm.data?.zones.find((z) => z.id === zoneId);
  const { hash } = useLocation();
  // Dose on an Overview card opens this page at the dose panel, once the zone has loaded
  useEffect(() => {
    if (zone && hash === `#${DOSE_CARD}`) goToDoseCard();
  }, [zone, hash]);

  return (
    <>
      <div className="phead">
        <div>
          <nav className="crumbs" aria-label="Breadcrumb">
            <Link to="/">Overview</Link> / {zone?.name ?? zoneId}
          </nav>
          <div className="title-row">
            <h1>{zone?.name ?? "Zone"}</h1>
            {zone && <ZoneSourceChip source={zone.source} />}
          </div>
          {zone?.source === "LIVE" && farm.data?.features.playground && (
            <p>Real hardware, on real time. The simulator doesn’t change this zone.</p>
          )}
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
          <ZoneLive key={`live-${zone.id}`} zone={zone} farm={farm.data} />

          {hasClimateController(farm.data) && <ZoneClimateCard farm={farm.data} />}

          <ZoneDetails key={`details-${zone.id}`} zone={zone} />

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
