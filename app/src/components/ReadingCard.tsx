import type { Quality } from "../api/types";
import { formatReading, QUALITY_TEXT, type ReadingSpec } from "../config/readings";

export type Range = { min: number; max: number };
export type ReadingTone = "ok" | "warn" | "off";

/** Grey when there is nothing trustworthy to show, amber outside the target band, green otherwise. */
function readingTone(
  value: number | null | undefined,
  quality: Quality | undefined,
  target: Range | undefined,
  stale: boolean,
): ReadingTone {
  if (stale || value === null || value === undefined) return "off";
  if (quality && quality !== "OK") return quality === "OUT_OF_RANGE" ? "warn" : "off";
  if (target && (value < target.min || value > target.max)) return "warn";
  return "ok";
}

function statusText(
  tone: ReadingTone,
  value: number | null | undefined,
  quality: Quality | undefined,
  target: Range | undefined,
  stale: boolean,
  loading: boolean,
) {
  if (loading) return "Loading";
  if (quality && quality !== "OK") return QUALITY_TEXT[quality];
  if (tone === "off") return stale ? QUALITY_TEXT.STALE : "No data";
  // say which way it is off, so the grower knows whether to raise or lower it
  if (tone === "warn") return target && value! < target.min ? "Too low" : "Too high";
  return target ? "In target" : undefined;
}

/** One reading: label, value with unit, target band and a status line, coloured by quality and target. */
export function ReadingCard({
  spec,
  value,
  quality,
  target,
  stale = false,
  loading = false,
}: {
  spec: ReadingSpec;
  value: number | null | undefined;
  quality?: Quality;
  target?: Range;
  /** controller offline or readings old: grey, whatever the value says */
  stale?: boolean;
  /** still waiting for the first answer */
  loading?: boolean;
}) {
  const tone = readingTone(value, quality, target, stale);
  const status = statusText(tone, value, quality, target, stale, loading);
  return (
    <div className={`reading reading-${tone}`} role="group" aria-label={spec.label}>
      <span className="reading-head">
        <span>{spec.label}</span>
        {target && (
          <span>
            Target {formatReading(target.min, spec)}–{formatReading(target.max, spec)}
          </span>
        )}
      </span>
      <span className="reading-value">
        <b>{formatReading(value, spec)}</b>
        {spec.unit && <small>{spec.unit}</small>}
      </span>
      {status && <span className="reading-status">{status}</span>}
    </div>
  );
}
