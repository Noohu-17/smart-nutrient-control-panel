import { useId } from "react";
import { useSimReset, useSimSpeed } from "../../api/hooks";
import type { SimState } from "../../api/types";
import { can, WHY } from "../../auth/roles";
import { useSession } from "../../auth/session";
import { SPEEDS, speedLabel } from "../../config/sim";
import { LockReason, SimButton } from "./SimButton";

/** Speed (Pause, ×1, ×60, ×288) for Owner, Agronomist and Operator; Reset for the Owner (§11.4). */
export function ClockCard({ sim }: { sim: SimState }) {
  const { role } = useSession();
  const speed = useSimSpeed();
  const reset = useSimReset();
  const speedReasonId = useId();
  const resetReasonId = useId();
  const speedLocked = role === null || !can(role, "sim") ? WHY.sim : undefined;
  const resetLocked = role === null || !can(role, "simReset") ? WHY.simReset : undefined;
  const error = speed.error ?? reset.error;

  return (
    <section className="zone-card" aria-labelledby="sim-clock-title">
      <header className="zone-card-head">
        <h2 id="sim-clock-title">Clock</h2>
      </header>

      <div className="sim-row" role="group" aria-label="Speed">
        {SPEEDS.map((x) => (
          <SimButton
            key={x}
            pressed={sim.speed === x}
            reasonId={speedLocked && speedReasonId}
            disabled={speed.isPending}
            onClick={() => speed.mutate(x)}
          >
            {x === 0 ? "Pause" : speedLabel(x)}
          </SimButton>
        ))}
      </div>
      <LockReason id={speedReasonId} reason={speedLocked} />

      <div className="sim-row">
        <SimButton
          reasonId={resetLocked && resetReasonId}
          disabled={reset.isPending}
          onClick={() => reset.mutate(undefined)}
        >
          Reset
        </SimButton>
        <span className="dose-note">Back to the start of {sim.scenario}: drills off, values released.</span>
      </div>
      <LockReason id={resetReasonId} reason={resetLocked} />

      {error && <p className="banner crit">{error.message}</p>}
    </section>
  );
}
