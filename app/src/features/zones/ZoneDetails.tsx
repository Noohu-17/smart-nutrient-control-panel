import type { ReactNode } from "react";
import { useFarm } from "../../api/hooks";
import type { FarmZone } from "../../api/types";
import {
  cropDayText,
  cropName,
  linkName,
  plantedText,
  sensorName,
  systemName,
  tankDetail,
  titleCase,
  unitDetail,
  zoneProfile,
} from "../../config/zoneText";
import { useZoneStatus } from "./useZoneStatus";

function Row({ term, children }: { term: string; children: ReactNode }) {
  return (
    <div>
      <dt>{term}</dt>
      <dd>{children}</dd>
    </div>
  );
}

/** What the zone is and grows, from the config pack (T1-38, T2-30). Rows the pack leaves out are
    left out here too. */
export function ZoneDetails({ zone }: { zone: FarmZone }) {
  const farm = useFarm();
  const status = useZoneStatus(zone.id);
  const profile = zoneProfile(zone, status.t?.profile);
  const crop = status.t?.crop;
  const sensors = Object.entries(profile.sensors);
  const fitted = sensors.filter(([, on]) => on).map(([name]) => sensorName(name));
  const notFitted = sensors.filter(([, on]) => !on).map(([name]) => sensorName(name));
  const day = crop ? cropDayText(crop, farm.data?.timezone ?? "UTC", new Date()) : null;

  return (
    <section className="zone-card" aria-labelledby="details-title">
      <header className="zone-card-head">
        <h2 id="details-title">Zone details</h2>
      </header>
      <dl className="zone-details">
        <Row term="Crop">
          {crop ? [cropName(crop), crop.variety].filter(Boolean).join(" · ") : "No recipe applied"}
        </Row>
        {crop && <Row term="Stage">{titleCase(crop.stage)}</Row>}
        {crop?.plantedOn && (
          <Row term="Planted">{[plantedText(crop.plantedOn), day].filter(Boolean).join(" · ")}</Row>
        )}
        {profile.growingSystem && <Row term="Growing system">{systemName(profile.growingSystem)}</Row>}
        {profile.growingUnits.length > 0 && (
          <Row term="Growing units">
            {profile.growingUnits.map((unit, i) => (
              <span key={i} className="zone-details-line">
                {unitDetail(unit)}
              </span>
            ))}
          </Row>
        )}
        {profile.plantSites !== null && <Row term="Plant sites">{profile.plantSites}</Row>}
        {profile.size && (
          <Row term="Zone size">
            {profile.size.lengthM} × {profile.size.widthM} m
          </Row>
        )}
        {profile.position && (
          <Row term="Position">
            {profile.position.label || `${profile.position.xM} m, ${profile.position.yM} m from the corner`}
          </Row>
        )}
        <Row term="Tank">
          {profile.tank.id} · {tankDetail(profile.tank)}
        </Row>
        {sensors.length > 0 && (
          <Row term="Sensors fitted">
            {fitted.length ? fitted.join(", ") : "None"}
            {notFitted.length > 0 && (
              <span className="zone-details-line">Not fitted: {notFitted.join(", ")}</span>
            )}
          </Row>
        )}
        <Row term="Controller">
          {profile.controller} · {linkName(profile.link)}
        </Row>
        {profile.notes && <Row term="Notes">{profile.notes}</Row>}
      </dl>
    </section>
  );
}
