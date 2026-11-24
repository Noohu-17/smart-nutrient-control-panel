import { useClimate, useSensors } from "../../api/hooks";
import type { ClimateStatus, Farm, FarmZone, SensorInfo } from "../../api/types";
import { StatusChip } from "../../components/StatusChip";
import {
  formatSensorValue,
  isFitted,
  NECESSITY_TEXT,
  readingValue,
  readsClimate,
  sensorSource,
  zoneFarmType,
} from "./sensorText";
import { ZoneSourceChip } from "./ZoneLive";
import { useZoneStatus, type ZoneStatus } from "./useZoneStatus";

/** One sensor: a fitted one shows its live reading, Live or Simulated; one that isn't fitted is
    greyed out, "Not connected", with what it would measure. */
function SensorCard({
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
  const fitted = isFitted(sensor, zone, farm);
  const fromClimate = sensor.fittedBy === "CLIMATE_CONTROLLER";
  // old values grey: the zone (or for the air, the climate controller) silent or past fresh
  const old = fromClimate ? climate?.online === false : status.offline || status.stale;

  return (
    <li className={`sensor-card${fitted ? "" : " not-connected"}`} aria-label={sensor.name}>
      <div className="sensor-card-head">
        <b>{sensor.name}</b>
        {fitted ? (
          <ZoneSourceChip source={sensorSource(sensor, zone, farm)} />
        ) : (
          <StatusChip tone="off">Not connected</StatusChip>
        )}
      </div>
      {fitted && sensor.readings.length > 0 && (
        <div className={`sensor-values${old ? " old" : ""}`}>
          {sensor.readings.map((reading) => (
            <span key={reading.key} className="reading-value">
              {sensor.readings.length > 1 && <small>{reading.label}</small>}
              <b>{formatSensorValue(reading, readingValue(reading, status.t, climate))}</b>
              {reading.unit && <small>{reading.unit}</small>}
            </span>
          ))}
        </div>
      )}
      <p className="sensor-measures">
        {fitted
          ? sensor.measures
          : `Would measure: ${sensor.measures.charAt(0).toLowerCase()}${sensor.measures.slice(1)}`}
      </p>
      <p className="sensor-meta">
        Typical {sensor.typical} · {NECESSITY_TEXT[sensor.necessity]}
      </p>
    </li>
  );
}

/** Every sensor of the zone's farm type (sensors.json, DM-3), fitted or not. None when the pack has
    no catalogue or none for this zone's growing system. */
export function ZoneSensors({ zone, farm }: { zone: FarmZone; farm: Farm }) {
  const catalogue = useSensors();
  const farmType = catalogue.data ? zoneFarmType(catalogue.data, zone) : null;
  if (!farmType) return null;
  return <SensorList zone={zone} farm={farm} name={farmType.name} sensors={farmType.sensors} />;
}

function SensorList({
  zone,
  farm,
  name,
  sensors,
}: {
  zone: FarmZone;
  farm: Farm;
  name: string;
  sensors: SensorInfo[];
}) {
  const status = useZoneStatus(zone.id);
  const climate = useClimate({ enabled: readsClimate(sensors) && Boolean(farm.climate.controller) });
  const connected = sensors.filter((s) => isFitted(s, zone, farm)).length;

  return (
    <section className="zone-card" aria-labelledby="sensors-title">
      <header className="zone-card-head">
        <h2 id="sensors-title">Sensors</h2>
        <StatusChip>{`${connected} of ${sensors.length} connected`}</StatusChip>
      </header>
      <p className="empty-note">{name}</p>
      <ul className="sensor-cards">
        {sensors.map((sensor) => (
          <SensorCard
            key={sensor.id}
            sensor={sensor}
            zone={zone}
            farm={farm}
            status={status}
            climate={climate.data}
          />
        ))}
      </ul>
    </section>
  );
}
