import { ReadingCard } from "../../components/ReadingCard";
import { StatusChip } from "../../components/StatusChip";
import { formatAge, ZONE_READINGS } from "../../config/readings";
import type { ZoneStatus } from "./useZoneStatus";

/** How fresh the zone's readings are: Offline, Loading, Stale or "Updated 30 s ago". */
export function ZoneStatusChip({ status }: { status: ZoneStatus }) {
  if (status.offline) return <StatusChip tone="off">Offline</StatusChip>;
  if (status.loading) return <StatusChip>Loading</StatusChip>;
  if (status.stale) return <StatusChip tone="warn">Stale</StatusChip>;
  return <StatusChip tone="ok">{`Updated ${formatAge(status.ageS ?? 0)}`}</StatusChip>;
}

/** What happened, what the farm did, what to do next (PRD rule 7). */
export function SafetyBanners({ zoneName, status }: { zoneName: string; status: ZoneStatus }) {
  const { t } = status;
  return (
    <>
      {t?.estop && (
        <div className="banner crit" role="alert">
          <b>E-STOP pressed in {zoneName}.</b> All dosing and refill stopped; circulation keeps running. Check
          the cabinet, release the E-STOP, then press Resume.
        </div>
      )}
      {t?.leak && (
        <div className="banner crit" role="alert">
          <b>Leak sensor wet in {zoneName}.</b> Check the zone for water and fix any leak.
        </div>
      )}
    </>
  );
}

/** The zone's reading tiles. Grey when offline or stale; "Loading" until the first answer. */
export function ZoneReadings({ status }: { status: ZoneStatus }) {
  const { t, loading, offline, stale } = status;
  return (
    <div className="readings">
      {ZONE_READINGS.map((spec) => (
        <ReadingCard
          key={spec.key}
          spec={spec}
          value={t?.readings[spec.key]}
          quality={t?.quality[spec.key]}
          stale={offline || stale}
          loading={loading}
        />
      ))}
    </div>
  );
}
