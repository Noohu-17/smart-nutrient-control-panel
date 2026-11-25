import { useId, useState } from "react";
import { useFarm, useOperations } from "../../api/hooks";
import type { Operation, OperationKind } from "../../api/types";
import { EmptyState } from "../../components/EmptyState";
import { RoleButton } from "../../components/RoleButton";
import { StatusChip } from "../../components/StatusChip";
import { clock, KIND_TEXT, readingsText, resultView, whatText, whereText, whoText } from "./operationText";

const KINDS = Object.keys(KIND_TEXT) as OperationKind[];

function OperationItem({ op, names }: { op: Operation; names: Map<string, string> }) {
  const result = resultView(op);
  const readings = readingsText(op);
  return (
    <li>
      <div>
        <b>{whatText(op)}</b>
        <span>
          {whereText(op, names)} · {whoText(op)}
        </span>
        <small>
          <time dateTime={op.at}>{clock(op.at)}</time>
          {readings && ` · ${readings}`}
          {result.reason && ` · ${result.reason}`}
        </small>
      </div>
      <StatusChip tone={result.tone}>{result.word}</StatusChip>
    </li>
  );
}

/** T2-23 · History: every dose, stop, resume and acknowledged alert, newest first, with when, the
    zone, what it was, who did it and the result (GET /api/operations, T1-28). Older ones a page at
    a time. Every role may read it (§11.2). */
export function History() {
  const ids = useId();
  const farm = useFarm();
  const zones = farm.data?.zones ?? [];
  const names = new Map(zones.map((zone) => [zone.id, zone.name]));
  const [kind, setKind] = useState<OperationKind | "">("");
  const [zone, setZone] = useState("");
  const operations = useOperations({ kind: kind || undefined, zone: zone || undefined });
  const items = operations.data?.pages.flatMap((page) => page.items) ?? [];
  const filtered = Boolean(kind || zone);

  return (
    <>
      <div className="phead">
        <h1>History</h1>
        <div className="actions">
          <RoleButton perm="export">Export CSV</RoleButton>
        </div>
      </div>

      <section className="zone-card" aria-labelledby={`${ids}-title`}>
        <header className="zone-card-head">
          <h2 id={`${ids}-title`}>Every action, newest first</h2>
        </header>
        <div className="sim-row">
          <label htmlFor={`${ids}-kind`}>Show</label>
          <select
            id={`${ids}-kind`}
            className="sim-select"
            value={kind}
            onChange={(e) => setKind(e.target.value as OperationKind | "")}
          >
            <option value="">Everything</option>
            {KINDS.map((k) => (
              <option key={k} value={k}>
                {KIND_TEXT[k]}
              </option>
            ))}
          </select>
          <label htmlFor={`${ids}-zone`}>Zone</label>
          <select
            id={`${ids}-zone`}
            className="sim-select"
            value={zone}
            onChange={(e) => setZone(e.target.value)}
          >
            <option value="">All zones</option>
            {zones.map((z) => (
              <option key={z.id} value={z.id}>
                {z.name}
              </option>
            ))}
          </select>
        </div>

        {operations.isError ? (
          <EmptyState title="Couldn’t load the history">{operations.error.message}</EmptyState>
        ) : operations.isPending || farm.isPending ? (
          // the zones' names too, so a row never shows "Z1" first and "Zone A" a moment later
          <EmptyState title="Loading the history…" />
        ) : items.length === 0 ? (
          <EmptyState title={filtered ? "Nothing matches" : "No operations yet"}>
            {filtered
              ? "No action of this kind or zone yet."
              : "Doses, stops, resumes and acknowledged alerts are listed here, with who did them and the result."}
          </EmptyState>
        ) : (
          <ul className="sim-list" aria-label="Operations">
            {items.map((op) => (
              <OperationItem key={`${op.kind}-${op.cmdId ?? op.alert?.id}-${op.at}`} op={op} names={names} />
            ))}
          </ul>
        )}

        {operations.hasNextPage && (
          <button
            type="button"
            className="btn"
            disabled={operations.isFetchingNextPage}
            onClick={() => void operations.fetchNextPage()}
          >
            {operations.isFetchingNextPage ? "Loading…" : "Load older"}
          </button>
        )}
      </section>
    </>
  );
}
