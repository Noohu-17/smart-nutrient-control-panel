import { describe, expect, it } from "vitest";
import type { DoseLineAmount, ZoneStatus } from "../api/types";
import { zoneDosing } from "./data/dosing";
import { buildZoneStatus, findZone } from "./data/telemetry";
import { runDoseChecks } from "./dosing";

const lines: DoseLineAmount[] = [{ line: "A", ml: 100 }];

function checkResult(id: string, change: Partial<ZoneStatus>) {
  const status = { ...buildZoneStatus("Z1")!, ...change };
  const dr = runDoseChecks("dr_test", findZone("Z1")!, status, zoneDosing("Z1")!, lines);
  return dr.checks.find((c) => c.id === id)!;
}

describe("fake dose checks: unknown means blocked (§8.3)", () => {
  it("passes STATE_KNOWN when the zone is not stopped", () => {
    expect(checkResult("STATE_KNOWN", { stopped: false }).result).toBe("PASS");
  });

  it("blocks a stopped zone until it is resumed", () => {
    const check = checkResult("STATE_KNOWN", { stopped: true });
    expect(check.result).toBe("BLOCK");
    expect(check.message).toContain("Resume the zone first");
  });

  it("blocks when the stopped state is not known yet", () => {
    const check = checkResult("STATE_KNOWN", { stopped: null });
    expect(check.result).toBe("BLOCK");
    expect(check.message).toContain("not known yet");
  });

  it("does not trust EC or pH without a quality", () => {
    expect(checkResult("FRESH_READINGS", { quality: {} }).result).toBe("BLOCK");
  });
});
