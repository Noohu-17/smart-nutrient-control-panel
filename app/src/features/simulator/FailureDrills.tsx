import { useId, useState } from "react";
import { useSimFault } from "../../api/hooks";
import type { FailureDrill, Farm, SimState } from "../../api/types";
import { can, WHY } from "../../auth/roles";
import { useSession } from "../../auth/session";
import { StatusChip } from "../../components/StatusChip";
import { drillTargets, targetLabel } from "../../config/sim";
import { LockReason, SimButton } from "./SimButton";

/** Failure drills from scenarios.json (§11.4.3): Trigger breaks a part, Fix repairs it.
    Running drills are listed first and marked in the catalogue. */
export function FailureDrills({ farm, sim, drills }: { farm: Farm; sim: SimState; drills: FailureDrill[] }) {
  const { role } = useSession();
  const fault = useSimFault();
  const reasonId = useId();
  const locked = role === null || !can(role, "sim") ? WHY.sim : undefined;
  const title = (kind: string) => drills.find((d) => d.kind === kind)?.title ?? kind;
  const fixText = (kind: string) => drills.find((d) => d.kind === kind)?.fix;

  return (
    <section className="zone-card" aria-labelledby="failure-drills-title">
      <header className="zone-card-head">
        <h2 id="failure-drills-title">Failure drills</h2>
      </header>
      <LockReason id={reasonId} reason={locked} />

      {sim.faults.length === 0 ? (
        <p className="empty-note">No failure drills are running.</p>
      ) : (
        <ul className="sim-list" aria-label="Running drills">
          {sim.faults.map((f) => (
            <li key={`${f.kind}/${f.target}`}>
              <div>
                <b>
                  {title(f.kind)} · {targetLabel(farm, f.target)}
                </b>
                {fixText(f.kind) && <span>Fix: {fixText(f.kind)}</span>}
              </div>
              <SimButton
                reasonId={locked && reasonId}
                disabled={fault.isPending}
                label={`Fix ${title(f.kind)} on ${targetLabel(farm, f.target)}`}
                onClick={() => fault.mutate({ ...f, on: false })}
              >
                Fix
              </SimButton>
            </li>
          ))}
        </ul>
      )}

      {fault.error && <p className="banner crit">{fault.error.message}</p>}

      {drills.length === 0 ? (
        <p className="empty-note">This site’s scenarios.json lists no failure drills.</p>
      ) : (
        <ul className="sim-list" aria-label="Drills you can trigger">
          {drills.map((drill) => (
            <DrillRow
              key={drill.kind}
              farm={farm}
              drill={drill}
              running={sim.faults.filter((f) => f.kind === drill.kind).map((f) => f.target)}
              reasonId={locked && reasonId}
              busy={fault.isPending}
              onTrigger={(target) => fault.mutate({ kind: drill.kind, target, on: true })}
            />
          ))}
        </ul>
      )}
    </section>
  );
}

function DrillRow({
  farm,
  drill,
  running,
  reasonId,
  busy,
  onTrigger,
}: {
  farm: Farm;
  drill: FailureDrill;
  running: string[];
  reasonId: string | undefined;
  busy: boolean;
  onTrigger: (target: string) => void;
}) {
  const targets = drillTargets(farm, drill);
  const [target, setTarget] = useState(targets[0] ?? "");
  const selectId = useId();
  const isRunning = running.includes(target);

  return (
    <li>
      <div>
        <span className="sim-title">
          <b>{drill.title}</b> <small>{drill.prdCode}</small>
        </span>
        <span>{drill.explanation}</span>
        {running.length > 0 && (
          <span>
            <StatusChip tone="warn">{`Running: ${running.map((t) => targetLabel(farm, t)).join(", ")}`}</StatusChip>
          </span>
        )}
      </div>
      <div className="sim-row">
        {targets.length > 1 && (
          <>
            <label className="sr-only" htmlFor={selectId}>
              {drill.title}: where
            </label>
            <select
              id={selectId}
              className="sim-select"
              value={target}
              disabled={Boolean(reasonId)}
              onChange={(e) => setTarget(e.target.value)}
            >
              {targets.map((t) => (
                <option key={t} value={t}>
                  {targetLabel(farm, t)}
                </option>
              ))}
            </select>
          </>
        )}
        <SimButton
          reasonId={reasonId}
          label={`${isRunning ? "Running" : "Trigger"} ${drill.title}`}
          disabled={busy || isRunning || targets.length === 0}
          onClick={() => onTrigger(target)}
        >
          {isRunning ? "Running" : "Trigger"}
        </SimButton>
      </div>
    </li>
  );
}
