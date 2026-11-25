import { noSimulator, useFarm, useScenarioOptions, useSimState } from "../../api/hooks";
import { Placeholder } from "../../components/Placeholder";
import { drillTargets, liveZones, SWITCH_DRILLS } from "../../config/sim";
import { ClockCard } from "./ClockCard";
import { FailureDrills } from "./FailureDrills";
import { FarmPlan } from "./FarmPlan";
import { HeldValues } from "./HeldValues";
import { SwitchDrills } from "./SwitchDrills";

/** Simulator screen, the farm's side of a playground site: speed, reset, held values and failure
    drills (§11.4). It has no buttons for people's actions: dose, refill and stop stay on the
    control panel (contract 11.4.1). Pressing the E-STOP is the farm's side, so it is here. */
export function SimulatorPage() {
  const farm = useFarm();
  const playground = farm.data?.features.playground === true;
  const sim = useSimState(playground);
  const options = useScenarioOptions(playground);
  if (farm.isPending) return <Placeholder title="Simulator" note="Loading the farm…" />;
  if (farm.isError)
    return <Placeholder title="Simulator" note={`Could not load the farm: ${farm.error.message}`} />;
  // features.playground false, or the back end's 404 for a site with no playground (T1-20)
  if (!playground || noSimulator(sim.error) || noSimulator(options.error))
    return <Placeholder title="Simulator not available" note="The simulator is only on playground sites." />;

  const controlsError = sim.error ?? options.error;
  // a drill with nothing left to aim at (every target in a live zone) isn't offered
  const drills = (options.data?.failureDrills ?? []).filter((d) => drillTargets(farm.data, d).length > 0);
  const switches = drills.filter((d) => d.kind in SWITCH_DRILLS);
  const live = liveZones(farm.data);
  return (
    <>
      <div className="phead">
        <h1>Simulator</h1>
        {sim.data && <p>{sim.data.scenario}</p>}
      </div>

      {live.length > 0 && (
        <p className="banner info" role="note">
          {live.map((z) => z.name).join(", ")} {live.length === 1 ? "is" : "are"} live: real hardware, on real
          time. The simulator can’t hold values, run drills or change the speed there, so{" "}
          {live.length === 1 ? "it is" : "they are"} left out of these controls, and Reset is off.
        </p>
      )}

      {controlsError ? (
        <p className="banner crit">Could not reach the simulator: {controlsError.message}</p>
      ) : !sim.data || !options.data ? (
        <p className="empty-note">Loading the simulator…</p>
      ) : (
        <ClockCard sim={sim.data} resetOff={live.length > 0} />
      )}

      <section className="zone-card" aria-labelledby="farm-view-title">
        <header className="zone-card-head">
          <h2 id="farm-view-title">Farm view</h2>
        </header>
        <FarmPlan farm={farm.data} />
      </section>

      {sim.data && options.data && (
        <div className="zone-grid">
          {switches.length > 0 && <SwitchDrills farm={farm.data} sim={sim.data} drills={switches} />}
          <HeldValues farm={farm.data} sim={sim.data} holdable={options.data.holdable} />
          <FailureDrills
            farm={farm.data}
            sim={sim.data}
            drills={drills.filter((d) => !(d.kind in SWITCH_DRILLS))}
          />
        </div>
      )}
    </>
  );
}
