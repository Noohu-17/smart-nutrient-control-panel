// src/features/history/operationText.test.ts · T2-23 · how one action reads on the History page
import { describe, expect, it } from "vitest";
import type { Operation } from "../../api/types";
import { readingsText, resultView, whatText, whereText, whoText } from "./operationText";

const dose = (changes: Partial<Operation> = {}): Operation => ({
  kind: "DOSE",
  at: "2026-11-20T04:55:00.000Z",
  by: { id: "u1", name: "Ravi Operator", role: "OPERATOR" },
  zone: "Z1",
  target: { zone: "Z1" },
  cmdId: "c1",
  result: { state: "COMPLETED", reason: null },
  lines: [{ line: "A", requestedMl: 120, deliveredMl: 120, state: "DONE" }],
  before: { ec: 1.51, ph: 6.21 },
  after: { ec: 1.63, ph: 6.02 },
  alert: null,
  ...changes,
});

describe("History wording (T2-23)", () => {
  it("says what a dose did, with EC and pH before and after", () => {
    expect(whatText(dose())).toBe("Dose: Stock A 120 mL");
    expect(readingsText(dose())).toBe("EC 1.51 → 1.63 · pH 6.21 → 6.02");
    expect(whoText(dose())).toBe("Ravi Operator · Operator");
    expect(resultView(dose())).toEqual({ word: "Completed", tone: "ok", reason: "" });
  });

  it("waits for after while the mix wait runs, and doesn't for a dose that won't have one", () => {
    expect(readingsText(dose({ after: null, result: { state: "COMPLETED", reason: null } }))).toBe(
      "EC 1.51 → … · pH 6.21 → …",
    );
    const stopped = dose({ after: null, result: { state: "STOPPED", reason: "STOP_REQUESTED" } });
    expect(readingsText(stopped)).toBe("Before: EC 1.51 · pH 6.21");
    expect(readingsText(dose({ before: null }))).toBeNull(); // not reported yet
  });

  it("names where a stop or a resume reached when it has no zone", () => {
    const names = new Map([["Z1", "Zone A"]]);
    expect(whereText(dose(), names)).toBe("Zone A");
    expect(
      whereText({ ...dose(), kind: "STOP", zone: null, target: { scope: "ALL_OPERATIONS" } }, names),
    ).toBe("All zones");
    expect(whereText({ ...dose(), kind: "RESUME", zone: null, target: { scope: "CLIMATE" } }, names)).toBe(
      "Climate",
    );
  });

  it("shows someone the server no longer knows by their ID, and nobody as Unknown", () => {
    expect(whoText(dose({ by: { id: "u9", name: null, role: null } }))).toBe("u9");
    expect(whoText(dose({ by: null }))).toBe("Unknown");
  });
});
