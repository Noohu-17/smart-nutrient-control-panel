import { useFarm } from "../api/hooks";
import { FarmPlan } from "../features/simulator/FarmPlan";
import { Placeholder } from "./Placeholder";

/** Simulator screen, the farm's side of a playground site. It has no buttons for people's
    actions: dose, refill and stop stay on the control panel (contract 11.4.1). */
export function SimulatorPage() {
  const farm = useFarm();
  if (farm.isPending) return <Placeholder title="Simulator" note="Loading the farm…" />;
  if (farm.isError)
    return <Placeholder title="Simulator" note={`Could not load the farm: ${farm.error.message}`} />;
  if (!farm.data.features.playground)
    return <Placeholder title="Simulator not available" note="The simulator is only on playground sites." />;

  return (
    <>
      <div className="phead">
        <h1>Simulator</h1>
      </div>

      <section className="zone-card" aria-labelledby="farm-view-title">
        <header className="zone-card-head">
          <h2 id="farm-view-title">Farm view</h2>
        </header>
        <FarmPlan farm={farm.data} />
      </section>

      <div className="zone-grid">
        <section className="zone-card" aria-labelledby="held-values-title">
          <header className="zone-card-head">
            <h2 id="held-values-title">Held values</h2>
          </header>
          <p className="empty-note">No values are being held.</p>
        </section>

        <section className="zone-card" aria-labelledby="failure-drills-title">
          <header className="zone-card-head">
            <h2 id="failure-drills-title">Failure drills</h2>
          </header>
          <p className="empty-note">No failure drills are running.</p>
        </section>
      </div>
    </>
  );
}
