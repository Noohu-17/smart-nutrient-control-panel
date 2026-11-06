import { describe, expect, it } from "vitest";
import type { ZoneStatus } from "../../api/types";
import { buildZoneStatus } from "../../mocks/data/telemetry";
import { bySeverity, RESERVOIR_LOW_PCT, zoneAlerts } from "./zoneAlerts";

const ZONE = { id: "Z1", name: "Zone A" };

/** Zone A as the fake API has it, healthy, with some fields changed. */
function zone(change: (t: ZoneStatus) => void = () => {}) {
  const t = buildZoneStatus("Z1")!;
  change(t);
  return t;
}

const kinds = (t: ZoneStatus | undefined, unreachable = false) =>
  zoneAlerts(ZONE, t, unreachable).map((a) => a.kind);

describe("zoneAlerts", () => {
  it("has nothing to say about a healthy zone, or before the first answer", () => {
    expect(zoneAlerts(ZONE, zone())).toEqual([]);
    expect(zoneAlerts(ZONE, undefined)).toEqual([]);
  });

  it("E-STOP: critical, says what to do, and doesn't repeat the stop", () => {
    const [alert, ...rest] = zoneAlerts(
      ZONE,
      zone((t) => {
        t.estop = true;
        t.stopped = true;
      }),
    );
    expect(rest).toEqual([]);
    expect(alert).toMatchObject({ kind: "estop", severity: "crit", title: "E-STOP pressed in Zone A." });
    expect(alert.text).toContain("release the E-STOP, then press Resume");
  });

  it("stopped zone: check it, then press Resume", () => {
    const [alert] = zoneAlerts(
      ZONE,
      zone((t) => (t.stopped = true)),
    );
    expect(alert).toMatchObject({ kind: "stopped", severity: "warn", title: "Zone A is stopped." });
    expect(alert.text).toContain("Check the zone, then press Resume.");
  });

  it("sensor FAULT names the sensors", () => {
    const [alert] = zoneAlerts(
      ZONE,
      zone((t) => {
        t.readings.ec = null;
        t.readings.ph = null;
        t.quality.ec = "FAULT";
        t.quality.ph = "FAULT";
      }),
    );
    expect(alert).toMatchObject({ kind: "sensor", title: "EC and pH sensor faults in Zone A." });
    expect(alert.text).toContain("Check the probe and its cable.");
  });

  it("one STALE reading is a stuck or late probe; all STALE is the whole zone", () => {
    const [one] = zoneAlerts(
      ZONE,
      zone((t) => (t.quality.ec = "STALE")),
    );
    expect(one.title).toBe("EC reading is not updating in Zone A.");

    const [all] = zoneAlerts(
      ZONE,
      zone((t) => {
        for (const key of Object.keys(t.quality) as (keyof ZoneStatus["quality"])[]) t.quality[key] = "STALE";
      }),
    );
    expect(all.title).toBe("No fresh readings from Zone A.");
    expect(all.text).toMatch(/^The last one was at .+\. The controller is still connected/);
  });

  it(`reservoir below ${RESERVOIR_LOW_PCT} %, but not at it or from a bad level sensor`, () => {
    const [low] = zoneAlerts(
      ZONE,
      zone((t) => (t.readings.reservoirPct = 22)),
    );
    expect(low).toMatchObject({
      kind: "reservoir",
      severity: "warn",
      title: "Zone A reservoir is low (22 %).",
    });
    expect(low.text).toContain("Refill the reservoir.");

    expect(kinds(zone((t) => (t.readings.reservoirPct = RESERVOIR_LOW_PCT)))).toEqual([]);
    expect(
      kinds(
        zone((t) => {
          t.readings.reservoirPct = 5;
          t.quality.reservoirPct = "OUT_OF_RANGE";
        }),
      ),
    ).toEqual([]);
  });

  it("offline controller: one critical alert, not one per old reading", () => {
    const alerts = zoneAlerts(
      ZONE,
      zone((t) => {
        t.online = false;
        t.lastSeen = "2026-11-06T10:42:00Z";
        t.readings.reservoirPct = 10;
        t.quality.ec = "STALE";
      }),
    );
    expect(alerts.map((a) => a.kind)).toEqual(["offline"]);
    expect(alerts[0]).toMatchObject({ severity: "crit", title: "Zone A controller is offline." });
    expect(alerts[0].text).toMatch(/^Last heard at .+\. .*Check the controller’s power and network cable\.$/);
  });

  it("a zone the app can't fetch at all", () => {
    expect(zoneAlerts(ZONE, undefined, true)[0]).toMatchObject({
      kind: "offline",
      title: "Can’t get Zone A’s readings.",
    });
  });

  it("puts critical first", () => {
    expect(
      kinds(
        zone((t) => {
          t.stopped = true;
          t.readings.reservoirPct = 10;
          t.leak = true;
        }),
      ),
    ).toEqual(["leak", "stopped", "reservoir"]);

    const zoneB = zoneAlerts({ id: "Z2", name: "Zone B" }, { ...zone(), zone: "Z2", estop: true });
    const zoneA = zoneAlerts(
      ZONE,
      zone((t) => (t.stopped = true)),
    );
    expect(bySeverity([...zoneA, ...zoneB]).map((a) => a.id)).toEqual(["Z2-estop", "Z1-stopped"]);
  });

  it("clears once the zone reports the condition gone", () => {
    expect(kinds(zone((t) => (t.estop = t.stopped = true)))).toEqual(["estop"]);
    expect(kinds(zone((t) => (t.estop = false)))).toEqual([]);
  });
});
