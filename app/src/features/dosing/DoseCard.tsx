import { useId, useState } from "react";
import { useConfirmDose, useDoseReview } from "../../api/hooks";
import type { DoseLineAmount, DoseRequest, FarmZone } from "../../api/types";
import { can, WHY } from "../../auth/roles";
import { useSession } from "../../auth/session";
import { LockedButton } from "../../components/LockedButton";
import { StatusChip } from "../../components/StatusChip";
import { checkTitle, lineName, RESULT_MARK, RESULT_TEXT, RESULT_TONE } from "../../config/dosing";

type Amounts = Record<string, string>;

const toLines = (zone: FarmZone, amounts: Amounts): DoseLineAmount[] =>
  zone.doseLines.map((line) => ({
    line,
    ml: amounts[line] === "" || amounts[line] === undefined ? 0 : Number(amounts[line]),
  }));

const isWholeMl = (v: string | undefined) => v === undefined || v === "" || /^\d+$/.test(v);

/** Manual dose on the Zone page: amounts per line → safety checks (§11.3) → confirm → sent. */
export function DoseCard({ zone }: { zone: FarmZone }) {
  const { role } = useSession();
  const [amounts, setAmounts] = useState<Amounts>({});
  const [ticked, setTicked] = useState(false);
  // the review on screen; kept while Re-check runs so the list does not flash away
  const [dr, setDr] = useState<DoseRequest | null>(null);
  // one key per review, so a repeated Start returns the first result, never a second dose (§11.1)
  const [confirmKey, setConfirmKey] = useState("");
  const review = useDoseReview();
  const confirm = useConfirmDose();
  const idBase = useId();

  const allowed = role !== null && can(role, "operate");
  const lines = toLines(zone, amounts);
  const valid = zone.doseLines.every((l) => isWholeMl(amounts[l])) && lines.some((l) => l.ml > 0);

  function runChecks() {
    setTicked(false);
    confirm.reset();
    review.mutate(
      { zone: zone.id, lines },
      {
        onSuccess: (next) => {
          setDr(next);
          setConfirmKey(crypto.randomUUID());
        },
      },
    );
  }
  function edit() {
    setDr(null);
    review.reset();
    confirm.reset();
    setTicked(false);
  }

  return (
    <section className="zone-card" id="dose-card" aria-labelledby="dose-title">
      <header className="zone-card-head">
        <h2 id="dose-title">{dr ? "Review safety checks" : "Manual dose"}</h2>
      </header>

      {confirm.data ? (
        <Sent cmdId={confirm.data.cmdId} onDone={edit} />
      ) : dr ? (
        <Review
          dr={dr}
          ticked={ticked}
          setTicked={setTicked}
          sending={confirm.isPending}
          error={(confirm.error ?? review.error)?.message}
          rechecking={review.isPending}
          onEdit={edit}
          onRecheck={runChecks}
          onStart={() => confirm.mutate({ id: dr.id, key: confirmKey })}
        />
      ) : (
        <form
          className="dose-form"
          onSubmit={(e) => {
            e.preventDefault();
            if (valid && allowed) runChecks();
          }}
        >
          <div className="dose-lines">
            {zone.doseLines.map((line) => (
              <div key={line} className="dose-line">
                <label htmlFor={`${idBase}-${line}`}>{lineName(line)}</label>
                <span className="dose-input">
                  <input
                    id={`${idBase}-${line}`}
                    type="number"
                    inputMode="numeric"
                    min={0}
                    step={1}
                    value={amounts[line] ?? ""}
                    disabled={!allowed}
                    aria-invalid={!isWholeMl(amounts[line])}
                    onChange={(e) => setAmounts({ ...amounts, [line]: e.target.value })}
                  />
                  <small>mL</small>
                </span>
              </div>
            ))}
          </div>
          {review.error && <p className="banner crit">{review.error.message}</p>}
          <div className="dose-actions">
            <span className="dose-note">Enter whole mL. Limits are checked on the next step.</span>
            {allowed ? (
              <button type="submit" className="btn primary" disabled={!valid || review.isPending}>
                {review.isPending ? "Checking…" : "Review dose"}
              </button>
            ) : (
              <LockedButton reason={WHY.operate}>Review dose</LockedButton>
            )}
          </div>
        </form>
      )}
    </section>
  );
}

type ReviewProps = {
  dr: DoseRequest;
  ticked: boolean;
  setTicked: (v: boolean) => void;
  sending: boolean;
  rechecking: boolean;
  error: string | undefined;
  onEdit: () => void;
  onRecheck: () => void;
  onStart: () => void;
};

function Review({
  dr,
  ticked,
  setTicked,
  sending,
  rechecking,
  error,
  onEdit,
  onRecheck,
  onStart,
}: ReviewProps) {
  const errors = dr.checks.filter((c) => c.result === "BLOCK");
  const warns = dr.checks.filter((c) => c.result === "WARN");
  return (
    <>
      <ul className="dose-summary">
        {dr.lines
          .filter((l) => l.ml > 0)
          .map((l) => (
            <li key={l.line}>
              <span>{lineName(l.line)}</span>
              <b>{l.ml} mL</b>
            </li>
          ))}
      </ul>

      <ul className="checks" aria-label="Safety checks">
        {dr.checks.map((c) => (
          <li key={c.id} className={c.result.toLowerCase()}>
            <span className="check-mark" aria-hidden="true">
              {RESULT_MARK[c.result]}
            </span>
            <div>
              <b>{checkTitle(c.id)}</b>
              <span>{c.message}</span>
            </div>
            <StatusChip tone={RESULT_TONE[c.result]}>{RESULT_TEXT[c.result]}</StatusChip>
          </li>
        ))}
      </ul>

      <dl className="dose-facts">
        <div>
          <dt>Total</dt>
          <dd>{dr.estimate.totalMl} mL</dd>
        </div>
        <div>
          <dt>Pumping and mixing</dt>
          <dd>{dr.estimate.durationMin} min</dd>
        </div>
        <div>
          <dt>Projected after mixing</dt>
          <dd>
            EC {dr.projected.ec ?? "--"} · pH {dr.projected.ph ?? "--"}
          </dd>
        </div>
      </dl>

      {errors.length > 0 ? (
        <div className="banner crit" role="alert">
          <b>Dose error.</b> {errors.map((c) => c.message).join(". ")}.
        </div>
      ) : (
        <label className="dose-confirm">
          <input type="checkbox" checked={ticked} onChange={(e) => setTicked(e.target.checked)} />
          <span>
            I have checked the quantities{warns.length ? " and the warnings above" : ""} and want to start
            this dose
          </span>
        </label>
      )}
      {error && (
        <p className="banner crit" role="alert">
          {error}
        </p>
      )}

      <div className="dose-actions">
        <button type="button" className="btn" onClick={onEdit}>
          Edit quantities
        </button>
        <button type="button" className="btn" onClick={onRecheck} disabled={rechecking}>
          {rechecking ? "Checking…" : "Re-check"}
        </button>
        <button
          type="button"
          className="btn primary"
          disabled={!dr.canConfirm || !ticked || sending}
          onClick={onStart}
        >
          {sending ? "Sending…" : "Start dose"}
        </button>
      </div>
    </>
  );
}

function Sent({ cmdId, onDone }: { cmdId: string; onDone: () => void }) {
  return (
    <>
      <div className="banner info" role="status">
        <b>Sent — waiting for the farm.</b> The controller runs its own checks before it starts. Command{" "}
        {cmdId}.
      </div>
      <div className="dose-actions">
        <button type="button" className="btn" onClick={onDone}>
          New dose
        </button>
      </div>
    </>
  );
}
