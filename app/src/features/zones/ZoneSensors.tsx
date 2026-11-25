import { useClimate, useSensors } from "../../api/hooks";
import type { ClimateStatus, Farm, FarmZone, Quality, SensorInfo, SensorReading } from "../../api/types";
import { ReadingCard } from "../../components/ReadingCard";
import { StatusChip } from "../../components/StatusChip";
import { ZONE_READINGS, type ReadingSpec } from "../../config/readings";
import {
  formatSensorValue,
  isFitted,
  readingValue,
  readsClimate,
  sensorSource,
  zoneFarmType,
} from "./sensorText";
import { readingTarget } from "./readingTarget";
import { ZoneReadings } from "./ZoneLive";
import type { ZoneStatus } from "./useZoneStatus";

/** The reading card's spec: the app's own for the zone's main readings (the Overview uses the same
    labels), otherwise the catalogue's label, unit and decimals. */
function specOf(reading: SensorReading): ReadingSpec {
  const own = reading.from === "zone.readings" ? ZONE_READINGS.find((s) => s.key === reading.key) : undefined;
  if (own) return own;
  return {
    key: reading.key,
    label: reading.label,
    unit: reading.unit ?? "",
    decimals: reading.decimals ?? 1,
  };
}

/** A yes/no reading such as the leak sensor: Dry in green, Wet in red. */
function StateCard({
  reading,
  value,
  old,
}: {
  reading: SensorReading;
  value: boolean | null | undefined;
  old: boolean;
}) {
  const tone = old || value === null || value === undefined ? "off" : value ? "crit" : "ok";
  return (
    <div className={`reading reading-${tone}`} role="group" aria-label={reading.label}>
      <span className="reading-head">
        <span>{reading.label}</span>
      </span>
      <span className="reading-value">
        <b>{formatSensorValue(reading, value)}</b>
      </span>
    </div>
  );
}

/** A sensor the zone doesn't have: greyed, "Not connected", with what it would measure; its typical
    range on hover, so the card stays as short as a live one. */
function NotConnectedCard({ sensor }: { sensor: SensorInfo }) {
  return (
    <div
      className="reading reading-off not-connected"
      role="group"
      aria-label={sensor.name}
      title={`Typical ${sensor.typical}`}
    >
      <span className="reading-head">
        <span>{sensor.name}</span>
      </span>
      <span className="reading-status">Not connected</span>
      <span className="reading-note">
        Would measure {sensor.measures.charAt(0).toLowerCase()}
        {sensor.measures.slice(1)}
      </span>
    </div>
  );
}

function FittedCards({
  sensor,
  zone,
  farm,
  status,
  climate,
}: {
  sensor: SensorInfo;
  zone: FarmZone;
  farm: Farm;
  status: ZoneStatus;
  climate?: ClimateStatus;
}) {
  const fromClimate = sensor.fittedBy === "CLIMATE_CONTROLLER";
  const old = fromClimate ? climate?.online === false : status.offline || status.stale;
  // only the air can be played by the simulator on a farm whose zones are live (client_B)
  const note = fromClimate && sensorSource(sensor, zone, farm) === "SIMULATED" ? "Simulated" : undefined;
  return (
    <>
      {sensor.readings.map((reading) => {
        const value = readingValue(reading, status.t, climate);
        if (typeof value === "boolean" || reading.states)
          return (
            <StateCard
              key={reading.key}
              reading={reading}
              value={value as boolean | null | undefined}
              old={old}
            />
          );
        const quality = (fromClimate ? climate?.quality : status.t?.quality) as
          Record<string, Quality> | undefined;
        return (
          <ReadingCard
            key={reading.key}
            spec={specOf(reading)}
            value={value as number | null | undefined}
            quality={quality?.[reading.key]}
            stale={old}
            loading={fromClimate ? !climate : status.loading}
            note={note}
            {...(reading.from === "zone.readings" ? readingTarget(status, reading.key) : {})}
            about={sensor.measures}
          />
        );
      })}
    </>
  );
}

/** The zone page's readings: every sensor of the zone's farm type (sensors.json, DM-3), the fitted
    ones live and the rest greyed "Not connected". A pack without a catalogue, or none for this
    zone's growing system, shows the zone's main readings as before. */
export function ZoneSensorReadings({
  zone,
  farm,
  status,
}: {
  zone: FarmZone;
  farm: Farm;
  status: ZoneStatus;
}) {
  const catalogue = useSensors();
  const farmType = catalogue.data ? zoneFarmType(catalogue.data, zone) : null;
  const sensors = farmType?.sensors ?? [];
  const climate = useClimate({ enabled: readsClimate(sensors) && Boolean(farm.climate.controller) });
  if (!farmType) return <ZoneReadings status={status} />;

  const fitted = sensors.filter((s) => isFitted(s, zone, farm));
  const missing = sensors.filter((s) => !isFitted(s, zone, farm));
  return (
    <>
      <div className="readings">
        {fitted.map((sensor) => (
          <FittedCards
            key={sensor.id}
            sensor={sensor}
            zone={zone}
            farm={farm}
            status={status}
            climate={climate.data}
          />
        ))}
        {missing.map((sensor) => (
          <NotConnectedCard key={sensor.id} sensor={sensor} />
        ))}
      </div>
      <p className="readings-count">
        <StatusChip>{`${fitted.length} of ${sensors.length} sensors connected`}</StatusChip>
      </p>
    </>
  );
}
