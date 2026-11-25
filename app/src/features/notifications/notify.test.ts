import { describe, expect, it } from "vitest";
import type { ServerAlert } from "../../api/types";
import { dueNow, openSince, REPEAT_MS, toastOrder } from "./notify";

const alert = (
  id: string,
  severity: ServerAlert["severity"],
  status: ServerAlert["status"] = "OPEN",
): ServerAlert => ({
  id,
  kind: "EC_HIGH",
  severity,
  status,
  zone: "Z1",
  message: "EC high",
  systemAction: "No automatic action",
  nextStep: "Dilute",
  raisedAt: `2026-11-19T10:00:0${id.length}.000Z`,
  simTs: null,
  clearedAt: null,
  acknowledgedAt: null,
  acknowledgedBy: null,
});

describe("when an alert notifies", () => {
  it("notifies a new open alert once, for the chosen severities only", () => {
    const last = new Map<string, number>();
    const alerts = [alert("c", "CRITICAL"), alert("w", "WARNING")];
    expect(dueNow(alerts, ["CRITICAL"], last, 0).map((a) => a.id)).toEqual(["c"]);
    expect(dueNow(alerts, ["CRITICAL"], last, 1000)).toEqual([]);
  });

  it("repeats a critical alert nobody has acknowledged, but not a warning", () => {
    const last = new Map<string, number>();
    const alerts = [alert("c", "CRITICAL"), alert("w", "WARNING")];
    dueNow(alerts, ["CRITICAL", "WARNING"], last, 0);
    expect(dueNow(alerts, ["CRITICAL", "WARNING"], last, REPEAT_MS - 1)).toEqual([]);
    expect(dueNow(alerts, ["CRITICAL", "WARNING"], last, REPEAT_MS).map((a) => a.id)).toEqual(["c"]);
  });

  it("stops repeating once the alert is acknowledged, in any browser", () => {
    const last = new Map<string, number>();
    dueNow([alert("c", "CRITICAL")], ["CRITICAL"], last, 0);
    expect(dueNow([alert("c", "CRITICAL", "ACKNOWLEDGED")], ["CRITICAL"], last, REPEAT_MS)).toEqual([]);
  });

  it("notifies again when a cleared alert comes back", () => {
    const last = new Map<string, number>();
    dueNow([alert("c", "CRITICAL")], ["CRITICAL"], last, 0);
    dueNow([alert("c", "CRITICAL", "CLEARED")], ["CRITICAL"], last, 1);
    expect(dueNow([alert("c", "CRITICAL")], ["CRITICAL"], last, 2).map((a) => a.id)).toEqual(["c"]);
  });

  it("doesn't pop up what was open at start, but still repeats it", () => {
    const last = openSince([alert("c", "CRITICAL")], 0);
    expect(dueNow([alert("c", "CRITICAL")], ["CRITICAL"], last, 1)).toEqual([]);
    expect(dueNow([alert("c", "CRITICAL")], ["CRITICAL"], last, REPEAT_MS)).toHaveLength(1);
  });

  it("puts critical first, then the newest", () => {
    const sorted = [alert("w", "WARNING"), alert("c", "CRITICAL"), alert("cc", "CRITICAL")].sort(toastOrder);
    expect(sorted.map((a) => a.id)).toEqual(["cc", "c", "w"]);
  });
});
