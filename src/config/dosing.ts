import type { CheckResult } from "../api/types";
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
