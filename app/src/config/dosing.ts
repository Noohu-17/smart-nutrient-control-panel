import type { CheckResult, Command, CommandState, LineState } from "../api/types";
import type { ChipTone } from "../components/StatusChip";

/* Display text only. Which lines a zone has comes from doseLines in GET /api/farm, and
   every limit comes from the check messages, so nothing here decides what may be dosed. */

const LINE_NAMES: Record<string, string> = { A: "Stock A", B: "Stock B", ACID: "Acid" };

/** "Stock A" for a known line; any other line from the config still shows as "Line X". */
export const lineName = (line: string) => LINE_NAMES[line] ?? `Line ${line}`;

/** Headings for the §11.3 check IDs; an ID the back end adds later shows as itself. */
const CHECK_TITLES: Record<string, string> = {
  STATE_KNOWN: "Zone state known",
  DEVICES_ONLINE: "Devices online",
  ESTOP: "E-STOP released",
  ZONE_REMOTE: "Zone in REMOTE",
  NO_CONFLICT: "No conflicting operation",
  LIMIT_DOSE: "Within per-dose limits",
  LIMIT_24H: "Within 24 h limits",
  DAYTANK: "Enough in day-tanks",
  RESERVOIR: "Reservoir level",
  PROJECTED_EC_PH: "Projected EC and pH",
  FRESH_READINGS: "Fresh readings",
};

export const checkTitle = (id: string) => CHECK_TITLES[id] ?? id;

/* The API value stays BLOCK (contract §11.3); people see it as "Error". */
export const RESULT_TEXT: Record<CheckResult, string> = { PASS: "Pass", WARN: "Warn", BLOCK: "Error" };
export const RESULT_MARK: Record<CheckResult, string> = { PASS: "✓", WARN: "!", BLOCK: "✕" };
export const RESULT_TONE: Record<CheckResult, ChipTone> = { PASS: "ok", WARN: "warn", BLOCK: "crit" };

type CommandView = { label: string; word: string; tone: ChipTone; closeWith?: string };

/** What the app shows for each state (contract §8.1). */
export const COMMAND_VIEW: Record<CommandState, CommandView> = {
  DISPATCHED: { label: "Sent — waiting for the farm", word: "Sent", tone: "neutral" },
  ACCEPTED: { label: "Accepted on site", word: "Accepted", tone: "neutral" },
  EXECUTING: { label: "Running", word: "Running", tone: "neutral" },
  COMPLETED: { label: "Dose finished", word: "Completed", tone: "ok", closeWith: "Done" },
  PARTIAL: { label: "Partial dose", word: "Partial", tone: "warn", closeWith: "Acknowledge" },
  FAILED: { label: "Dose failed", word: "Device error", tone: "crit", closeWith: "Acknowledge" },
  STOPPED: { label: "Dose stopped", word: "Stopped", tone: "warn", closeWith: "Acknowledge" },
  REJECTED: { label: "Dose refused", word: "Rejected", tone: "crit", closeWith: "Back to the form" },
  EXPIRED: {
    label: "Not accepted in time — not sent again",
    word: "Expired",
    tone: "warn",
    closeWith: "Acknowledge",
  },
  UNKNOWN: { label: "Result unknown — farm not responding", word: "Unknown", tone: "warn" },
};

/** UNKNOWN is not final: it always waits for the farm (§8.1). */
export const isFinalCommand = (state: CommandState) => COMMAND_VIEW[state].closeWith !== undefined;

/** The lines have finished and the reservoir is mixing: `after` comes in a second report (§6.6). */
export const isMixing = (command: Command) =>
  (command.state === "COMPLETED" || command.state === "PARTIAL") && command.after === null;

/** Shown instead of "Dose finished" while the mix wait runs. */
export const MIXING_VIEW: CommandView = {
  label: "Lines finished — mixing, then re-measuring",
  word: "Mixing",
  tone: "neutral",
  closeWith: "Done",
};

/** Stopping is always allowed (PRD rule 1), including while the result is unknown. */
export const stopsAreOffered = (state: CommandState) => !isFinalCommand(state);

export const LINE_STATE_TEXT: Record<LineState, string> = {
  PENDING: "Queued",
  RUNNING: "Running",
  DONE: "Done",
  SHORT: "Short",
  SKIPPED: "Skipped",
};

/** The §8.4 reason codes in plain words; a code added later shows as itself. */
const REASON_TEXT: Record<string, string> = {
  DEVICE_OFFLINE: "The controller or its sensors were not heard from recently.",
  ESTOP: "An E-STOP is pressed.",
  NOT_REMOTE: "The zone key switch is not in REMOTE.",
  BUSY: "Another dose or refill is running in the zone.",
  LIMIT_DOSE: "A line is over its per-dose limit.",
  LIMIT_24H: "A line would pass its 24-hour limit.",
  DAYTANK_LOW: "There is not enough concentrate in a day-tank.",
  RESERVOIR_LOW: "The reservoir is below the minimum to dose.",
  SENSOR_STALE: "The EC or pH reading is too old or faulty to dose on.",
  EXPIRED_ON_ARRIVAL: "The command reached the controller too late.",
  NO_FLOW: "The pump ran but no flow was measured.",
  FLOW_SHORT: "A line delivered less than requested.",
  TIMEOUT: "The dose ran out of time.",
  STOP_REQUESTED: "A stop command arrived.",
  ESTOP_DURING: "An E-STOP was pressed while it ran.",
  MODE_CHANGED_DURING: "The zone key switch left REMOTE while it ran.",
  REBOOT_MID_DOSE: "The controller restarted while it ran.",
  STATE_UNKNOWN: "The zone's state isn't known.",
  ZONE_STOPPED: "The zone was stopped and hasn't been resumed.",
  FAULT_LATCHED: "A latched fault must be cleared first.",
};

export const reasonText = (code: string | null) => (code ? (REASON_TEXT[code] ?? code) : "");
