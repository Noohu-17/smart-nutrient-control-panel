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
