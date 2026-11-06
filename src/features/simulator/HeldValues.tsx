import { useId, useState } from "react";
import { useSimHold, useSimRelease, useSimReleaseAll } from "../../api/hooks";
import type { Farm, ScenarioOptions, SimState } from "../../api/types";
import { can, WHY } from "../../auth/roles";
import { useSession } from "../../auth/session";
import { holdMetrics, holdTargets, metricLabel, metricUnit, targetLabel } from "../../config/sim";
import { LockReason, SimButton } from "./SimButton";

/** Set a value and hold it (§11.4.2): a zone or the polyhouse, a metric, a value inside the
    range scenarios.json allows. Held until released, one at a time or all together. */
export function HeldValues({
  farm,
  sim,
  holdable,
}: {
  farm: Farm;
  sim: SimState;
  holdable: ScenarioOptions["holdable"];
}) {
  const { role } = useSession();
  const hold = useSimHold();
  const release = useSimRelease();
  const releaseAll = useSimReleaseAll();
  const reasonId = useId();
  const ids = useId();
  const locked = role === null || !can(role, "sim") ? WHY.sim : undefined;

  const targets = holdTargets(farm);
  const [target, setTarget] = useState(targets[0] ?? "");
  const metrics = holdMetrics(farm, holdable, target);
  const [metric, setMetric] = useState(metrics[0]?.metric ?? "");
  const [value, setValue] = useState("");
  const [gradual, setGradual] = useState(true);

  const range = metrics.find((m) => m.metric === metric)?.range;
  const number = Number(value);
  const valid = value.trim() !== "" && range !== undefined && number >= range.min && number <= range.max;
  const unit = metricUnit(metric);
  const error = hold.error ?? release.error ?? releaseAll.error;

  function pickTarget(next: string) {
    setTarget(next);
    const nextMetrics = holdMetrics(farm, holdable, next);
    if (!nextMetrics.some((m) => m.metric === metric)) setMetric(nextMetrics[0]?.metric ?? "");
  }

  return (
    <section className="zone-card" aria-labelledby="held-values-title">
      <header className="zone-card-head">
        <h2 id="held-values-title">Held values</h2>
        {sim.holds.length > 0 && (
          <SimButton
            reasonId={locked && reasonId}
            disabled={releaseAll.isPending}
            onClick={() => releaseAll.mutate(undefined)}
          >
            Release all
          </SimButton>
        )}
      </header>
      <LockReason id={reasonId} reason={locked} />

      {sim.holds.length === 0 ? (
        <p className="empty-note">No values are being held.</p>
      ) : (
        <ul className="sim-list" aria-label="Values held now">
          {sim.holds.map((h) => {
            const name = `${targetLabel(farm, h.target)} · ${metricLabel(h.metric)}`;
            return (
              <li key={`${h.target}/${h.metric}`}>
                <div>
                  <b>{name}</b>
                  <span>
                    Held at {h.value}
                    {metricUnit(h.metric) && ` ${metricUnit(h.metric)}`}
                  </span>
                </div>
                <SimButton
                  reasonId={locked && reasonId}
                  label={`Release ${name}`}
                  disabled={release.isPending}
                  onClick={() => release.mutate({ target: h.target, metric: h.metric })}
                >
                  Release
                </SimButton>
              </li>
            );
          })}
        </ul>
      )}

      <form
        className="sim-form"
        aria-label="Hold a value"
        onSubmit={(e) => {
          e.preventDefault();
          if (!valid || locked) return;
          hold.mutate({ target, metric, value: number, gradual }, { onSuccess: () => setValue("") });
        }}
      >
        <label htmlFor={`${ids}-target`}>Where</label>
        <select
          id={`${ids}-target`}
          className="sim-select"
          value={target}
          disabled={Boolean(locked)}
          onChange={(e) => pickTarget(e.target.value)}
        >
          {targets.map((t) => (
            <option key={t} value={t}>
              {targetLabel(farm, t)}
            </option>
          ))}
        </select>

        <label htmlFor={`${ids}-metric`}>Reading</label>
        <select
          id={`${ids}-metric`}
          className="sim-select"
          value={metric}
          disabled={Boolean(locked)}
          onChange={(e) => setMetric(e.target.value)}
        >
          {metrics.map((m) => (
            <option key={m.metric} value={m.metric}>
              {metricLabel(m.metric)}
            </option>
          ))}
        </select>

        <label htmlFor={`${ids}-value`}>Value</label>
        <span className="dose-input">
          <input
            id={`${ids}-value`}
            type="number"
            inputMode="decimal"
            step="any"
            min={range?.min}
            max={range?.max}
            value={value}
            disabled={Boolean(locked)}
            aria-invalid={value !== "" && !valid}
            aria-describedby={`${ids}-range`}
            onChange={(e) => setValue(e.target.value)}
          />
          {unit && <small>{unit}</small>}
        </span>
        <span id={`${ids}-range`} className="dose-note sim-wide">
          {range ? `Between ${range.min} and ${range.max}${unit ? ` ${unit}` : ""}` : ""}
        </span>

        <label className="sim-check sim-wide">
          <input
            type="checkbox"
            checked={gradual}
            disabled={Boolean(locked)}
            onChange={(e) => setGradual(e.target.checked)}
          />
          Change gradually, as real water or air would
        </label>

        <div className="sim-wide">
          {locked ? (
            <SimButton reasonId={reasonId} onClick={() => {}}>
              Hold
            </SimButton>
          ) : (
            <button type="submit" className="btn primary" disabled={!valid || hold.isPending}>
              {hold.isPending ? "Holding…" : "Hold"}
            </button>
          )}
        </div>
      </form>

      {error && <p className="banner crit">{error.message}</p>}
    </section>
  );
}
