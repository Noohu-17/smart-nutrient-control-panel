import { useClimate, useFarm } from "../../api/hooks";
import { EmptyState } from "../../components/EmptyState";
import { ReadingCard } from "../../components/ReadingCard";
import { RoleButton } from "../../components/RoleButton";
import { AIR_READINGS, airPositionSpec, ELEC_ROOM_READING } from "../../config/readings";

/** Climate page: air inside and outside, each air sensor position and the electrical room (contract §6.2). */
export function Climate() {
  const farm = useFarm();
  const climate = useClimate();
  const c = climate.data;
  const positions = farm.data?.climate.airSensors ?? [];

  return (
    <>
      <div className="phead">
        <h1>Climate</h1>
        <div className="actions">
          <RoleButton perm="operate">Change mode</RoleButton>
          <RoleButton perm="operate">Control equipment</RoleButton>
        </div>
      </div>
      {climate.isPending ? (
        <EmptyState title="Loading climate readings…" />
      ) : climate.isError ? (
        <EmptyState title="No climate readings yet">
          Air temperature, humidity and equipment show here once the climate controller reports.
        </EmptyState>
      ) : c ? (
        <>
          <section className="climate-section" aria-labelledby="climate-air">
            <h2 id="climate-air">Air</h2>
            <div className="readings">
              {AIR_READINGS.map((spec) => (
                <ReadingCard
                  key={spec.key}
                  spec={spec}
                  value={c.readings[spec.key]}
                  quality={c.quality[spec.key]}
                />
              ))}
            </div>
          </section>
          {positions.length > 0 && (
            <section className="climate-section" aria-labelledby="climate-positions">
              <h2 id="climate-positions">Air temp along the house</h2>
              <div className="readings">
                {positions.map((pos) => (
                  <ReadingCard key={pos} spec={airPositionSpec(pos)} value={c.readings.airTempByPosC[pos]} />
                ))}
              </div>
            </section>
          )}
          <section className="climate-section" aria-labelledby="climate-elec">
            <h2 id="climate-elec">Electrical room</h2>
            <div className="readings">
              <ReadingCard
                spec={ELEC_ROOM_READING}
                value={c.readings.elecRoomTempC}
                quality={c.quality.elecRoomTempC}
              />
            </div>
          </section>
        </>
      ) : null}
    </>
  );
}
