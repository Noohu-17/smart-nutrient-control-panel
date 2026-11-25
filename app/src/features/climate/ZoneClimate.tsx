import { useClimate, useFarm } from "../../api/hooks";
import type { Farm } from "../../api/types";
import { ReadingCard } from "../../components/ReadingCard";
import { RoleButton } from "../../components/RoleButton";
import {
  AIR_READINGS,
  airPositionSpec,
  climateVpd,
  ELEC_ROOM_READING,
  EMPTY_VALUE,
  formatReading,
  VPD_READING,
} from "../../config/readings";
import { ZoneSourceChip } from "../zones/ZoneLive";
import { climateSource, fanStageText, hasClimateController } from "./climateText";

const [AIR_TEMP, AIR_RH] = AIR_READINGS;

/** The house climate on each zone page, from GET /api/climate: air inside and outside, VPD, each
    air sensor position and the electrical room, and the equipment controls. One controller serves
    the whole house, so every zone shows the same values. While it is offline its last values show
    grey, never as the air now. */
export function ZoneClimateCard({ farm }: { farm: Farm }) {
  const climate = useClimate();
  const c = climate.data;
  const stale = c ? !c.online : false;
  const positions = farm.climate.airSensors ?? [];

  return (
    <section className="zone-card" aria-labelledby="climate-title">
      <header className="zone-card-head">
        <div className="title-row">
          <h2 id="climate-title">House climate</h2>
          {/* layout climate.source: the simulator may play the climate on a real farm (DM-2) */}
          <ZoneSourceChip source={climateSource(farm)} />
        </div>
        <RoleButton perm="operate">Control equipment</RoleButton>
      </header>
      {climate.isError ? (
        <p className="empty-note">
          No climate readings yet. They show here once the climate controller reports.
        </p>
      ) : (
        <>
          <div className="readings">
            {AIR_READINGS.map((spec) => (
              <ReadingCard
                key={spec.key}
                spec={spec}
                value={c?.readings[spec.key]}
                quality={c?.quality[spec.key]}
                stale={stale}
                loading={climate.isPending}
              />
            ))}
            <ReadingCard
              spec={VPD_READING}
              value={c ? climateVpd(c) : undefined}
              stale={stale}
              loading={climate.isPending}
            />
          </div>
          {positions.length > 0 && (
            <>
              <h3 className="card-subhead">Air temp along the house</h3>
              <div className="readings">
                {positions.map((pos) => (
                  <ReadingCard
                    key={pos}
                    spec={airPositionSpec(pos)}
                    value={c?.readings.airTempByPosC[pos]}
                    stale={stale}
                    loading={climate.isPending}
                  />
                ))}
                <ReadingCard
                  spec={ELEC_ROOM_READING}
                  value={c?.readings.elecRoomTempC}
                  quality={c?.quality.elecRoomTempC}
                  stale={stale}
                  loading={climate.isPending}
                />
              </div>
            </>
          )}
          {/* a site with no fans in its layout (client_B) has no fan stage to show */}
          {farm.climate.fans.length > 0 && (
            <dl className="zone-details">
              <div>
                <dt>Fan stage</dt>
                <dd>{c ? fanStageText(c, farm) : EMPTY_VALUE}</dd>
              </div>
            </dl>
          )}
        </>
      )}
    </section>
  );
}

/** The same climate as one short line on an Overview zone card: air temp, humidity, VPD. None on
    a site with no climate controller. */
export function ZoneClimateLine() {
  const farm = useFarm();
  return farm.data && hasClimateController(farm.data) ? <ClimateLine /> : null;
}

function ClimateLine() {
  const { data: c } = useClimate();
  if (!c) return null;
  return (
    <p className="zone-climate-line">
      <b>House climate</b> {formatReading(c.readings.airTempC, AIR_TEMP)} °C ·{" "}
      {formatReading(c.readings.airRh, AIR_RH)} % · VPD {formatReading(climateVpd(c), VPD_READING)} kPa
      {!c.online && " · offline"}
    </p>
  );
}
