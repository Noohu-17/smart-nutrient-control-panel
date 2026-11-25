import { describe, expect, it } from "vitest";
import type { ServerAlert } from "../../api/types";
import { mergeAlerts } from "./serverAlerts";
import type { ZoneAlert } from "./zoneAlerts";

const ZONES = [
  { id: "Z1", name: "Zone A" },
  { id: "Z2", name: "Zone B" },
];

const alert = (kind: string, changes: Partial<ServerAlert> = {}): ServerAlert => ({
  id: kind,
  kind,
  severity: "WARNING",
  status: "OPEN",
  zone: "Z1",
  message: `${kind} message`,
  systemAction: "No automatic action",
  nextStep: "Do this",
  raisedAt: "2026-11-12T05:10:04.000Z",
  simTs: null,
  clearedAt: null,
  acknowledgedAt: null,
  acknowledgedBy: null,
  ...changes,
});

const liveEstop: ZoneAlert = {
  id: "Z1-estop",
  zoneId: "Z1",
  zoneName: "Zone A",
  kind: "estop",
  severity: "crit",
  title: "E-STOP pressed in Zone A.",
  text: "Press Resume.",
};

describe("live and server alerts together", () => {
  it("doesn't show the server's E-STOP when the zone already shows it, and puts critical first", () => {
    const merged = mergeAlerts(
      [liveEstop],
      [alert("ESTOP_PRESSED", { severity: "CRITICAL" }), alert("EC_HIGH"), alert("CIRC_NO_FLOW", { severity: "CRITICAL", zone: "Z2" })],
      ZONES,
    );
    expect(merged.map((a) => a.title)).toEqual(["E-STOP pressed in Zone A.", "CIRC_NO_FLOW message.", "EC_HIGH message."]);
  });

  it("leaves out cleared and INFO alerts, and other zones' alerts", () => {
    const merged = mergeAlerts(
      [],
      [alert("A", { status: "CLEARED" }), alert("B", { severity: "INFO" }), alert("C", { zone: "Z2" })],
      [ZONES[0]],
    );
    expect(merged).toEqual([]);
  });

  it("keeps an acknowledged alert until its condition clears", () => {
    expect(mergeAlerts([], [alert("EC_HIGH", { status: "ACKNOWLEDGED" })], ZONES)).toHaveLength(1);
  });

  it("includes alerts that belong to no zone only for the whole site", () => {
    const climate = [alert("CHILLER_FAULT", { zone: null, severity: "CRITICAL" })];
    expect(mergeAlerts([], climate, ZONES)).toEqual([]);
    expect(mergeAlerts([], climate, ZONES, true)[0].text).toBe("No automatic action. Do this.");
  });
});
