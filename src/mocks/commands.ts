/* Fake dose commands. Each one moves through the §8 states on a timer, worked out from the time
   it was created, so GET /api/commands/{cmdId} answers like a farm reporting CMD_STATE (§6.6):
   COMPLETED first comes with after: null while the reservoir mixes, then again with after.
   A stop leaves the zone stopped until POST /api/resume, as on the farm (§6.8, §7.8). */
import type { Command, CommandLine, CommandState, DoseRequest } from "../api/types";

export type CommandEnding = "COMPLETED" | "REJECTED" | "FAILED" | "PARTIAL" | "UNKNOWN" | "EXPIRED";

export type CommandRecord = {
  cmdId: string;
  dr: DoseRequest;
  device: string;
  before: { ec: number | null; ph: number | null };
  ending: CommandEnding;
  createdAt: number;
  stoppedAt?: number;
};

const DEFAULT_STEP_MS = 1500;
const ACCEPT_STEP = 1;
const FIRST_LINE_STEP = 2;
const MIX_STEPS = 2; // the mix wait, after the last line and before `after`

let stepMs = DEFAULT_STEP_MS;
const commands = new Map<string, CommandRecord>();
const stoppedZones = new Set<string>();

/** Tests run the same timeline faster. */
export function setCommandStepMs(ms: number) {
  stepMs = ms;
}

export function resetCommands() {
  commands.clear();
  stoppedZones.clear();
  stepMs = DEFAULT_STEP_MS;
}

/** A STOP or an E-STOP leaves the zone stopped until a person sends RESUME (§6.8). */
export const isZoneStopped = (zoneId: string) => stoppedZones.has(zoneId);
export const markZoneStopped = (zoneId: string) => stoppedZones.add(zoneId);
export const resumeZone = (zoneId: string) => stoppedZones.delete(zoneId);

export function addCommand(record: CommandRecord) {
  commands.set(record.cmdId, record);
}

export const findCommand = (cmdId: string) => commands.get(cmdId);

const NOT_RUNNING_STATES: CommandState[] = [
  "COMPLETED",
  "PARTIAL",
  "FAILED",
  "STOPPED",
  "REJECTED",
  "EXPIRED",
];

/** The zone's command that is still in flight or unconfirmed: it keeps the zone blocked (§8.3 rule 7). */
export function activeCommandId(zoneId: string, now = Date.now()) {
  for (const record of commands.values()) {
    if (record.dr.zone === zoneId && !NOT_RUNNING_STATES.includes(commandAt(record, now).state))
      return record.cmdId;
  }
  return null;
}

/** A stop reaches the farm only if it is still running there; a silent farm reports nothing back. */
export function stopZoneCommands(zoneId: string, now = Date.now()) {
  markZoneStopped(zoneId);
  for (const record of commands.values()) {
    if (record.dr.zone !== zoneId || record.stoppedAt !== undefined) continue;
    const { state } = commandAt(record, now);
    if (state === "DISPATCHED" || state === "ACCEPTED" || state === "EXECUTING") record.stoppedAt = now;
  }
}

/** Where each line is at a step of the timeline. Lines run one at a time, in order. */
function linesAt(record: CommandRecord, step: number): CommandLine[] {
  return record.dr.lines
    .filter((l) => l.ml > 0)
    .map((l, i) => {
      const lineStep = step - FIRST_LINE_STEP - i;
      const done = lineStep > 0;
      const running = lineStep === 0;
      return {
        line: l.line,
        requestedMl: l.ml,
        deliveredMl: done ? l.ml : running ? Math.round(l.ml / 2) : 0,
        state: done ? "DONE" : running ? "RUNNING" : "PENDING",
      };
    });
}

/** Cut the lines short: what was running stops at what it delivered, what was queued is skipped. */
const cutShort = (lines: CommandLine[]): CommandLine[] =>
  lines.map((l) => {
    if (l.state === "RUNNING") return { ...l, state: "SHORT" };
    if (l.state === "PENDING") return { ...l, state: "SKIPPED" };
    return l;
  });

/** The command as the farm reports it at this moment. */
export function commandAt(record: CommandRecord, now = Date.now()): Command {
  const step = Math.floor((now - record.createdAt) / stepMs);
  const lineCount = record.dr.lines.filter((l) => l.ml > 0).length;
  const mixStep = FIRST_LINE_STEP + lineCount;
  const time = (s: number) => new Date(record.createdAt + s * stepMs).toISOString();
  const base = {
    cmdId: record.cmdId,
    device: record.device,
    before: record.before,
    after: null,
    reason: null,
  };
  const at = (
    state: CommandState,
    lines: CommandLine[],
    reason: string | null = null,
    endStep: number | null = null,
  ): Command => ({
    ...base,
    state,
    lines,
    reason,
    startedAt: step >= FIRST_LINE_STEP && state !== "REJECTED" ? time(FIRST_LINE_STEP) : null,
    endedAt: endStep === null ? null : time(endStep),
  });

  if (record.stoppedAt !== undefined && step > ACCEPT_STEP) {
    const stopStep = Math.max(Math.floor((record.stoppedAt - record.createdAt) / stepMs), ACCEPT_STEP);
    return at("STOPPED", cutShort(linesAt(record, stopStep)), "STOP_REQUESTED", stopStep);
  }
  if (step < ACCEPT_STEP) return at("DISPATCHED", linesAt(record, step));

  if (record.ending === "REJECTED")
    return at("REJECTED", cutShort(linesAt(record, 0)), "LIMIT_24H", ACCEPT_STEP);
  if (record.ending === "EXPIRED") return at("EXPIRED", linesAt(record, 0));
  if (step < FIRST_LINE_STEP) return at("ACCEPTED", linesAt(record, step));

  if (record.ending === "FAILED" && step > FIRST_LINE_STEP) {
    const lines = linesAt(record, FIRST_LINE_STEP).map((l) => ({ ...l, deliveredMl: 0 }));
    return at("FAILED", cutShort(lines), "NO_FLOW", FIRST_LINE_STEP + 1);
  }
  if (record.ending === "PARTIAL" && step > FIRST_LINE_STEP + 1) {
    return at("PARTIAL", cutShort(linesAt(record, FIRST_LINE_STEP + 1)), "NO_FLOW", FIRST_LINE_STEP + 2);
  }
  if (record.ending === "UNKNOWN" && step > FIRST_LINE_STEP) {
    return at("UNKNOWN", linesAt(record, FIRST_LINE_STEP));
  }
  if (step < mixStep) return at("EXECUTING", linesAt(record, step));
  // the last line has ended: COMPLETED with after null while it mixes, then with after (§6.6)
  const completed = at("COMPLETED", linesAt(record, mixStep), null, mixStep);
  if (step < mixStep + MIX_STEPS) return completed;
  return { ...completed, after: { ec: record.dr.projected.ec, ph: record.dr.projected.ph } };
}
