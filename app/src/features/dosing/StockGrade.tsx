import { useState, type KeyboardEvent } from "react";
import { useSetStockNpk } from "../../api/hooks";
import type { DoseLineLimit, Npk } from "../../api/types";
import { can } from "../../auth/roles";
import { useSession } from "../../auth/session";
import { Icon } from "../../components/Icon";
import { lineName } from "../../config/dosing";

const PARTS = ["n", "p", "k"] as const;
type Draft = Record<keyof Npk, string>;

const toDraft = (npk: Npk | null | undefined): Draft => ({
  n: npk ? String(npk.n) : "",
  p: npk ? String(npk.p) : "",
  k: npk ? String(npk.k) : "",
});

/** The draft as a grade, or why it isn't one: each a % from 0 to 100, together no more than 100. */
function parse(draft: Draft): Npk | string {
  const values = PARTS.map((part) => (draft[part].trim() === "" ? NaN : Number(draft[part])));
  if (values.some((v) => !Number.isFinite(v) || v < 0 || v > 100)) return "Each is a % from 0 to 100";
  if (values.reduce((a, b) => a + b, 0) > 100) return "Together they can't be more than 100 %";
  const [n, p, k] = values;
  return { n, p, k };
}

/** Edit beside a stock's N-P-K, for an Owner or Agronomist (PUT /api/stocks/{line}/npk): e.g. after
    a new product goes into the drum. Saved on the server for every zone; others see the grade only.
    Not a <form>: it sits inside the dose form, so Enter saves here instead of reviewing the dose. */
export function StockGradeEdit({ info }: { info: DoseLineLimit }) {
  const { role } = useSession();
  const save = useSetStockNpk();
  const [draft, setDraft] = useState<Draft | null>(null);
  // acid only moves pH: it has no N-P-K to set
  if (!role || !can(role, "stockGrade") || info.line === "ACID") return null;

  const name = lineName(info.line);
  if (!draft)
    return (
      <button
        type="button"
        className="link-btn"
        aria-label={`Edit ${name}'s N-P-K`}
        onClick={() => {
          save.reset();
          setDraft(toDraft(info.npk));
        }}
      >
        <Icon name="pencil" size={14} /> {info.npk ? "Edit N-P-K" : "Add N-P-K"}
      </button>
    );

  const grade = parse(draft);
  const valid = typeof grade !== "string";
  const submit = () => {
    if (valid) save.mutate({ line: info.line, npk: grade }, { onSuccess: () => setDraft(null) });
  };
  const onKeyDown = (e: KeyboardEvent) => {
    if (e.key === "Enter") {
      e.preventDefault();
      submit();
    } else if (e.key === "Escape") setDraft(null);
  };
  return (
    <fieldset className="npk-edit" aria-label={`${name}'s N-P-K`}>
      <div className="npk-fields">
        {PARTS.map((part) => (
          <label key={part}>
            <span>{part.toUpperCase()} %</span>
            <input
              type="number"
              inputMode="decimal"
              min={0}
              max={100}
              step={0.1}
              value={draft[part]}
              aria-invalid={!valid}
              onChange={(e) => setDraft({ ...draft, [part]: e.target.value })}
              onKeyDown={onKeyDown}
            />
          </label>
        ))}
      </div>
      {!valid && <small className="dose-limit over">{grade}</small>}
      {save.error && (
        <small className="dose-limit over" role="alert">
          {save.error.message}
        </small>
      )}
      <div className="npk-actions">
        <button type="button" className="btn primary" disabled={!valid || save.isPending} onClick={submit}>
          {save.isPending ? "Saving…" : "Save"}
        </button>
        <button type="button" className="btn" onClick={() => setDraft(null)}>
          Cancel
        </button>
      </div>
    </fieldset>
  );
}
