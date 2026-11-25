import type { ZoneStatus } from "./useZoneStatus";

/** The crop stage's target for a zone reading, EC and pH only, and EC's in ppm on a controller
    with a TDS probe: what a reading card shows as "Target …" and judges "In target" by. */
export function readingTarget(status: ZoneStatus, key: string) {
  const crop = status.t?.crop;
  const target = key === "ec" ? crop?.ecTarget : key === "ph" ? crop?.phTarget : undefined;
  const ppm = status.t?.profile?.tdsPpmPerMsCm;
  const note =
    key === "ec" && target && ppm
      ? `${Math.round(target.min * ppm)}–${Math.round(target.max * ppm)} ppm`
      : undefined;
  return { target: target ?? undefined, targetNote: note };
}
