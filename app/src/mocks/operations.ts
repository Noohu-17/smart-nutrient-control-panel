/* The fake API's operations log (T2-23): every dose, stop, resume and acknowledgement it was sent,
   with who did it, answered as GET /api/operations does (contract §11.2, T1-28): newest first by
   time and then by ID, `kind` and `zone`, `limit` (1 to 500, default 100), and `before` a page's
   `next` or a time. A dose's result follows its fake command (commands.ts); the fake farm acts on a
   stop or a resume at once, so those are COMPLETED. Like the real back end, a playground reset
   doesn't clear it. */
import type { Operation, OperationKind, OperationList, ServerAlert } from "../api/types";
import { commandAt, findCommand } from "./commands";

type Entry = Omit<Operation, "result" | "lines" | "before" | "after"> & { atMs: number };

const DEFAULT_LIMIT = 100;
const MAX_LIMIT = 500;
const entries: Entry[] = [];

export function resetOperations() {
  entries.length = 0;
}

export function recordOperation(entry: Omit<Entry, "at" | "atMs">, now = Date.now()) {
  entries.push({ ...entry, at: new Date(now).toISOString(), atMs: now });
}

export const ackEntry = (
  alert: ServerAlert,
): Pick<Entry, "kind" | "zone" | "target" | "cmdId" | "alert"> => ({
  kind: "ACK",
  zone: alert.zone,
  target: null,
  cmdId: null,
  alert: { id: alert.id, kind: alert.kind, severity: alert.severity, message: alert.message },
});

const idOf = (e: { cmdId: string | null; alert: Operation["alert"] }) => e.cmdId ?? e.alert?.id ?? "";

function answer(entry: Entry, now: number): Operation {
  const { kind, at, by, zone, target, cmdId, alert } = entry;
  const rest = { kind, at, by, zone, target, cmdId, alert };
  const record = entry.kind === "DOSE" && entry.cmdId ? findCommand(entry.cmdId) : undefined;
  if (record) {
    const command = commandAt(record, now);
    return {
      ...rest,
      result: { state: command.state, reason: command.reason },
      lines: command.lines,
      before: command.before,
      after: command.after,
    };
  }
  const state = entry.kind === "ACK" ? "ACKNOWLEDGED" : "COMPLETED";
  return { ...rest, result: { state, reason: null }, lines: null, before: null, after: null };
}

/** One page, as the real back end answers it; null when the query isn't one (a 422 there). */
export function listOperations(query: URLSearchParams, now = Date.now()): OperationList | null {
  const kind = query.get("kind");
  const zone = query.get("zone");
  const limit = Number(query.get("limit") ?? DEFAULT_LIMIT);
  const before = query.get("before");
  if (kind && !["DOSE", "STOP", "RESUME", "ACK"].includes(kind)) return null;
  if (!Number.isInteger(limit) || limit < 1 || limit > MAX_LIMIT) return null;

  let cursor: { at: number; id: string | null } | null = null;
  if (before) {
    const [time, id] = before.split("~");
    const at = Date.parse(time);
    if (Number.isNaN(at)) return null;
    cursor = { at, id: id || null };
  }

  const found = entries
    .filter((e) => !kind || e.kind === (kind as OperationKind))
    .filter((e) => !zone || e.zone === zone || (e.kind === "STOP" && e.target?.scope !== "ZONE"))
    .filter((e) => {
      if (!cursor) return true;
      if (e.atMs !== cursor.at) return e.atMs < cursor.at;
      return cursor.id !== null && idOf(e) < cursor.id;
    })
    .sort((a, b) => b.atMs - a.atMs || (idOf(b) > idOf(a) ? 1 : idOf(b) < idOf(a) ? -1 : 0));

  const page = found.slice(0, limit);
  const last = page.at(-1);
  return {
    items: page.map((e) => answer(e, now)),
    next: found.length > limit && last ? `${last.at}~${idOf(last)}` : null,
  };
}
