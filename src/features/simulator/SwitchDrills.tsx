import { useId } from "react";
import { useSimFault } from "../../api/hooks";
import type { FailureDrill, Farm, SimState } from "../../api/types";
import { can, WHY } from "../../auth/roles";
import { useSession } from "../../auth/session";
import { StatusChip } from "../../components/StatusChip";
import { drillTargets, SWITCH_DRILLS, targetLabel } from "../../config/sim";
import { LockReason, SimButton } from "./SimButton";

/** The E-STOP drill (press and release, per zone) and the power cut (per device), when
    scenarios.json lists them (§11.4.3). The same POST /sim/faults as any drill; a refusal, such as
    a drill the simulator hasn't built yet, shows the server's message. */
export function SwitchDrills({ farm, sim, drills }: { farm: Farm; sim: SimState; drills: FailureDrill[] }) {
  const { role } = useSession();
  const fault = useSimFault();
  const reasonId = useId();
  const locked = role === null || !can(role, "sim") ? WHY.sim : undefined;

  return (
    <section className="zone-card" aria-labelledby="switch-drills-title">
      <header className="zone-card-head">
        <h2 id="switch-drills-title">E-STOP and power</h2>
      </header>
      <LockReason id={reasonId} reason={locked} />

      {drills.map((drill) => {
        const words = SWITCH_DRILLS[drill.kind]!;
        return (
          <div key={drill.kind} className="sim-switch">
            <h3 id={`${reasonId}-${drill.kind}`}>{drill.title}</h3>
            <p className="dose-note">{drill.explanation}</p>
            <ul className="sim-list" aria-labelledby={`${reasonId}-${drill.kind}`}>
              {drillTargets(farm, drill).map((target) => {
                const on = sim.faults.some((f) => f.kind === drill.kind && f.target === target);
                const where = targetLabel(farm, target);
                return (
                  <li key={target}>
                    <div>
                      <b>{where}</b>
                      {on && (
                        <span>
                          <StatusChip tone="crit">{words.isOn}</StatusChip> {drill.fix}
                        </span>
                      )}
                    </div>
                    <SimButton
                      reasonId={locked && reasonId}
                      label={`${on ? words.off : words.on} on ${where}`}
                      disabled={fault.isPending}
                      onClick={() => fault.mutate({ kind: drill.kind, target, on: !on })}
                    >
                      {on ? words.off : words.on}
                    </SimButton>
                  </li>
                );
              })}
            </ul>
          </div>
        );
      })}

      {fault.error && <p className="banner crit">{fault.error.message}</p>}
    </section>
  );
}
