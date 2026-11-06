/* Fake dose checks for POST /api/dose-requests (§11.3), run against the fake telemetry the
   way the back end will. Check IDs and PASS / WARN / BLOCK follow the §11.3 example. */
import type { DoseCheck, DoseLineAmount, DoseRequest, FarmZone, ZoneStatus } from "../api/types";
import type { ZoneDosing } from "./data/dosing";

// rough mixing model for the fake projection: EC gained per mL of stock A or B in each litre,
// pH lost per mL of acid in each litre
const EC_PER_ML_PER_L = 0.39;
const PH_PER_ACID_ML_PER_L = 4.7;
const FRESH_READING_S = 120; // §14 "Fresh EC/pH to dose"

const round = (n: number, dp: number) => Math.round(n * 10 ** dp) / 10 ** dp;

export class DoseInputError extends Error {}

/** Whole, non-negative mL on lines the zone has, and at least one above zero (§1: volumes are whole mL). */
export function validateLines(zone: FarmZone, lines: DoseLineAmount[]) {
  for (const l of lines) {
    if (!zone.doseLines.includes(l.line))
      throw new DoseInputError(`${zone.name} has no dosing line ${l.line}`);
    if (!Number.isInteger(l.ml) || l.ml < 0) throw new DoseInputError(`Line ${l.line}: enter whole mL`);
  }
  if (!lines.some((l) => l.ml > 0)) throw new DoseInputError("Enter an amount for at least one line");
}

export function runDoseChecks(
  id: string,
  zone: FarmZone,
  t: ZoneStatus,
  d: ZoneDosing,
  lines: DoseLineAmount[],
  now = Date.now(),
): DoseRequest {
  const checks: DoseCheck[] = [];
  const add = (
    cid: string,
    ok: boolean,
    pass: string,
    fail: string,
    failResult: "WARN" | "BLOCK" = "BLOCK",
  ) => checks.push({ id: cid, result: ok ? "PASS" : failResult, message: ok ? pass : fail });
  const r = t.readings;
  const dosing = lines.filter((l) => l.ml > 0);
  const busy =
    t.activeCmdId !== null ||
    Object.entries(t.equipment).some(([k, v]) => /-(DOSE|REFILL)/.test(k) && (v === "ON" || v === "OPEN"));
  const secondsSince = (ts: string | null) =>
    ts === null ? null : Math.round((now - Date.parse(ts)) / 1000);
  const ageS = secondsSince(t.ts); // null: no reading yet
  const silentS = secondsSince(t.lastSeen);

  add(
    "STATE_KNOWN",
    t.stopped === false && t.activeCmdId === null,
    `${zone.name} state known: nothing unconfirmed, not stopped`,
    t.stopped === null
      ? `${zone.name} state not known yet`
      : t.stopped
        ? `${zone.name} is stopped. Resume the zone first`
        : `${zone.name} has a command still in flight`,
  );
  add(
    "DEVICES_ONLINE",
    t.online,
    `${zone.controller} and sensors online`,
    silentS === null
      ? `${zone.controller} has never reported`
      : `${zone.controller} not heard from for ${silentS} s`,
  );
  add(
    "ESTOP",
    t.estop === false,
    "No E-STOP pressed",
    t.estop === null ? `E-STOP state of ${zone.name} not known yet` : `E-STOP pressed in ${zone.name}`,
  );
  add(
    "ZONE_REMOTE",
    t.mode === "REMOTE",
    `${zone.name} is in REMOTE`,
    `${zone.name} key switch is in ${t.mode ?? "an unknown position"}`,
  );
  add("NO_CONFLICT", !busy, "Nothing else running", `Another dose or refill is running in ${zone.name}`);

  const overDose = dosing.filter((l) => l.ml > d.limits.maxPerDoseMl[l.line]);
  add(
    "LIMIT_DOSE",
    overDose.length === 0,
    "All lines within per-dose limits",
    overDose.map((l) => `Line ${l.line} exceeds ${d.limits.maxPerDoseMl[l.line]} mL per dose`).join("; "),
  );
  const over24 = dosing.filter((l) => d.dosed24hMl[l.line] + l.ml > d.limits.max24hMl[l.line]);
  add(
    "LIMIT_24H",
    over24.length === 0,
    "24 h totals within limits",
    over24
      .map(
        (l) =>
          `Line ${l.line} would reach ${d.dosed24hMl[l.line] + l.ml} mL of the ${d.limits.max24hMl[l.line]} mL 24 h limit`,
      )
      .join("; "),
  );

  const short = dosing.filter((l) => (r.dayTankL[l.line] ?? 0) * 1000 < l.ml);
  const low = dosing.filter((l) => (r.dayTankPct[l.line] ?? 0) < d.dayTankWarnPct);
  if (short.length)
    add("DAYTANK", false, "", short.map((l) => `Day-tank ${l.line} has less than ${l.ml} mL`).join("; "));
  else
    add(
      "DAYTANK",
      low.length === 0,
      "Enough concentrate in every day-tank",
      low.map((l) => `Day-tank ${l.line} at ${r.dayTankPct[l.line]}%, refill soon`).join("; "),
      "WARN",
    );

  const resOk = r.reservoirPct !== null && r.reservoirPct >= d.limits.minReservoirPctToDose;
  add(
    "RESERVOIR",
    resOk,
    `${zone.reservoir.id} at ${r.reservoirPct}%`,
    `${zone.reservoir.id} at ${r.reservoirPct ?? "--"}%, below the ${d.limits.minReservoirPctToDose}% minimum to dose`,
  );

  const litres = r.reservoirL ?? zone.reservoir.usableL;
  const stockMl = dosing.filter((l) => l.line !== "ACID").reduce((s, l) => s + l.ml, 0);
  const acidMl = dosing.find((l) => l.line === "ACID")?.ml ?? 0;
  const ec = r.ec === null ? null : round(r.ec + (stockMl * EC_PER_ML_PER_L) / litres, 2);
  const ph = r.ph === null ? null : round(r.ph - (acidMl * PH_PER_ACID_ML_PER_L) / litres, 1);
  const tg = d.target;
  const target = `(target ${tg.ecMin}–${tg.ecMax}, ${tg.phMin}–${tg.phMax})`;
  if (ec === null || ph === null)
    checks.push({
      id: "PROJECTED_EC_PH",
      result: "BLOCK",
      message: "Cannot project EC and pH without both readings",
    });
  else if (ec > d.limits.ecCeiling || ph < d.limits.phMin || ph > d.limits.phMax)
    checks.push({
      id: "PROJECTED_EC_PH",
      result: "BLOCK",
      message: `Projected EC ${ec}, pH ${ph} is outside the safety limits (EC up to ${d.limits.ecCeiling}, pH ${d.limits.phMin}–${d.limits.phMax})`,
    });
  else {
    const inTarget = ec >= tg.ecMin && ec <= tg.ecMax && ph >= tg.phMin && ph <= tg.phMax;
    checks.push({
      id: "PROJECTED_EC_PH",
      result: inTarget ? "PASS" : "WARN",
      message: `Projected EC ${ec}, pH ${ph} ${target}`,
    });
  }
   // only a reading marked OK can be dosed on, a missing quality is not OK
  const unreliable = (["ec", "ph"] as const).filter((k) => t.quality[k] !== "OK" || r[k] === null);
  const fresh = ageS !== null && ageS <= FRESH_READING_S && unreliable.length === 0;
  add(
    "FRESH_READINGS",
    fresh,
    `EC/pH ${ageS} s old`,
    unreliable.length
      ? `${unreliable.map((k) => (k === "ec" ? "EC" : "pH")).join(" and ")} reading not usable (${unreliable.map((k) => t.quality[k] ?? "missing").join(", ")})`
      : ageS === null
        ? "No EC/pH reading yet"
        : `EC/pH ${ageS} s old, more than ${FRESH_READING_S} s`,
  );

  const pumpMin = dosing.reduce((s, l) => s + l.ml / d.pumpMlPerMin[l.line], 0);
  return {
    id,
    zone: zone.id,
    lines,
    projected: { ec, ph },
    checks,
    estimate: {
      durationMin: Math.ceil(pumpMin + d.mixWaitS / 60),
      totalMl: dosing.reduce((s, l) => s + l.ml, 0),
    },
    canConfirm: !checks.some((c) => c.result === "BLOCK"),
  };
}
