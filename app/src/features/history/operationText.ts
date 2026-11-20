// src/features/history/operationText.ts · T2-23 · how one action reads on the History page.
// Every value comes from GET /api/operations (contract §11.2, T1-28); the words are the app's.
import type { Operation, OperationKind } from "../../api/types";
import { fromServerRole } from "../../auth/roles";
import type { ChipTone } from "../../components/StatusChip";
import { COMMAND_VIEW, lineName, reasonText } from "../../config/dosing";

export const KIND_TEXT: Record<OperationKind, string> = {
  DOSE: "Doses",
  STOP: "Stops",
  RESUME: "Resumes",
  ACK: "Acknowledged alerts",
};

const STOP_SCOPE: Record<string, string> = {
  ALL_OPERATIONS: "all operations",
  ALL_EQUIPMENT: "all equipment",
  ZONE: "the zone",
};

const ml = (n: number) => `${n} mL`;
const number = (n: number | null | undefined) => (n === null || n === undefined ? "…" : String(n));

/** "Dose: Stock A 120 mL · Stock B 120 mL", "Stop: all operations", "Acknowledged: EC in …". */
export function whatText(op: Operation): string {
  switch (op.kind) {
    case "DOSE": {
      const lines = (op.lines ?? []).map(
        (l) =>
          `${lineName(l.line)} ${l.deliveredMl === l.requestedMl ? ml(l.requestedMl) : `${l.deliveredMl} of ${ml(l.requestedMl)}`}`,
      );
      return lines.length ? `Dose: ${lines.join(" · ")}` : "Dose";
    }
    case "STOP":
      return `Stop: ${STOP_SCOPE[op.target?.scope ?? ""] ?? op.target?.scope ?? "everything"}`;
    case "RESUME":
      return op.target?.scope === "CLIMATE" ? "Resume: climate" : "Resume";
    case "ACK":
      return `Acknowledged: ${op.alert?.message ?? "an alert"}`;
  }
}

// A dose that ended like this never gets its `after` reading: the mix wait didn't run (§6.6).
const NO_AFTER = ["STOPPED", "FAILED", "REJECTED", "EXPIRED"];

/** A dose's EC and pH before and after: "EC 1.51 → 1.63 · pH 6.21 → 6.02", "…" while the mix wait
    runs, and only "Before: …" for a dose that won't have an after. */
export function readingsText(op: Operation): string | null {
  if (op.kind !== "DOSE" || !op.before) return null;
  if (!op.after && NO_AFTER.includes(op.result.state))
    return `Before: EC ${number(op.before.ec)} · pH ${number(op.before.ph)}`;
  return `EC ${number(op.before.ec)} → ${number(op.after?.ec)} · pH ${number(op.before.ph)} → ${number(op.after?.ph)}`;
}

/** The zone's name, or what the action reached when it has no zone. */
export function whereText(op: Operation, names: Map<string, string>): string {
  if (op.zone) return names.get(op.zone) ?? op.zone;
  if (op.kind === "STOP") return "All zones";
  if (op.kind === "RESUME") return "Climate";
  return "Site";
}

/** "Ravi Operator · Operator"; the user's ID if the server no longer knows their name. */
export function whoText(op: Operation): string {
  if (!op.by) return "Unknown";
  const role = op.by.role ? ` · ${fromServerRole(op.by.role)}` : "";
  return `${op.by.name ?? op.by.id}${role}`;
}

/** The result as a chip: a command's state in the app's words (§8.1), or Acknowledged. */
export function resultView(op: Operation): { word: string; tone: ChipTone; reason: string } {
  const reason = reasonText(op.result.reason);
  if (op.result.state === "ACKNOWLEDGED") return { word: "Acknowledged", tone: "ok", reason };
  const view = COMMAND_VIEW[op.result.state];
  return { word: view?.word ?? op.result.state, tone: view?.tone ?? "neutral", reason };
}

/** "20 Nov, 12:31" in this browser's time. */
export const clock = (iso: string) =>
  new Date(iso).toLocaleString([], { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
