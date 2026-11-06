import { useEffect, useState } from "react";
import { useZone } from "../../api/hooks";

const ONE_SECOND = 1000;

/** The clock, once a second, so "Updated 30 s ago" keeps counting between readings. */
function useNow() {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), ONE_SECOND);
    return () => clearInterval(timer);
  }, []);
  return now;
}

/** One zone's telemetry and how far to trust it. Stale and offline come from the server (§11.2). */
export function useZoneStatus(zoneId: string) {
  const telemetry = useZone(zoneId);
  const now = useNow();

  const t = telemetry.data;
  const ageS = t?.ts ? Math.max(0, Math.round((now - Date.parse(t.ts)) / ONE_SECOND)) : null;
  // the zone can't be fetched, or the controller is silent (its last values still come, greyed)
  const offline = telemetry.isError || t?.online === false;
  // the back end marks readings past the fresh limit, or from an offline controller, STALE (§11.2)
  const markedStale = t ? Object.values(t.quality).includes("STALE") : false;
  const stale = !offline && markedStale;

  return { t, ageS, loading: telemetry.isPending, offline, stale };
}

export type ZoneStatus = ReturnType<typeof useZoneStatus>;
