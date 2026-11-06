import { useFarm, useScenarioOptions, useSimState } from "../../api/hooks";
import { Placeholder } from "../../components/Placeholder";
import { ClockCard } from "./ClockCard";
import { FailureDrills } from "./FailureDrills";
import { FarmPlan } from "./FarmPlan";
import { HeldValues } from "./HeldValues";

/** Simulator screen, the farm's side of a playground site: speed, reset, held values and failure
    drills (§11.4). It has no buttons for people's actions: dose, refill and stop stay on the
    control panel (contract 11.4.1). */
export function SimulatorPage() {
  const farm = useFarm();
  const playground = farm.data?.features.playground === true;
  const sim = useSimState(playground);
  const options = useScenarioOptions(playground);
  if (farm.isPending) return <Placeholder title="Simulator" note="Loading the farm…" />;
  if (farm.isError)
    return <Placeholder title="Simulator" note={`Could not load the farm: ${farm.error.message}`} />;
  if (!playground)
    return <Placeholder title="Simulator not available" note="The simulator is only on playground sites." />;

  const controlsError = sim.error ?? options.error;
  return (
    <>
      <div className="phead">
        <h1>Simulator</h1>
        {sim.data && <p>{sim.data.scenario}</p>}
      </div>

      {controlsError ? (
        <p className="banner crit">Could not reach the simulator: {controlsError.message}</p>
      ) : !sim.data || !options.data ? (
        <p className="empty-note">Loading the simulator…</p>
      ) : (
        <ClockCard sim={sim.data} />
      )}

      <section className="zone-card" aria-labelledby="farm-view-title">
        <header className="zone-card-head">
          <h2 id="farm-view-title">Farm view</h2>
        </header>
        <FarmPlan farm={farm.data} />
      </section>

      {sim.data && options.data && (
        <div className="zone-grid">
          <HeldValues farm={farm.data} sim={sim.data} holdable={options.data.holdable} />
          <FailureDrills farm={farm.data} sim={sim.data} drills={options.data.failureDrills} />
        </div>
      )}
    </>
  );
}
