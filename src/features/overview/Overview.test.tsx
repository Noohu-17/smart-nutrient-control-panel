import { screen, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { buildZoneStatus } from "../../mocks/data/telemetry";
import { server } from "../../mocks/node";
import { renderAt } from "../../test/render";

// the fake API waits 300 ms per call, so allow more than the 1 s default
const WAIT = { timeout: 3000 };

const card = async (name: string) =>
  within((await screen.findByRole("heading", { name }, WAIT)).closest("section")!);

describe("Overview", () => {
  it("shows both zones with names from GET /api/farm", async () => {
    renderAt("/");
    const zoneA = await card("Zone A");
    expect(zoneA.getByRole("link", { name: "Zone A" })).toHaveAttribute("href", "/zone/Z1");
    expect(screen.getByRole("heading", { name: "Zone B" })).toBeInTheDocument();
  });

  it("shows EC, pH, solution temp and reservoir level from the fake telemetry", async () => {
    renderAt("/");
    const zoneA = await card("Zone A");
    expect(await zoneA.findByText(/^Updated/, undefined, WAIT)).toBeInTheDocument();
    // the fake readings drift a little on every request, so check near the Z1 seed and the decimals shown
    for (const [label, seed, decimals] of [
      ["EC", 1.62, 2],
      ["pH", 6.04, 2],
      ["Solution temp", 23.4, 1],
      ["Reservoir level", 78, 0],
    ] as const) {
      const shown = zoneA.getByRole("group", { name: label }).querySelector("b")!.textContent!;
      expect(shown.split(".")[1]?.length ?? 0).toBe(decimals);
      expect(Number(shown)).toBeCloseTo(seed, 0);
    }
  });

  it('shows a null reading as "--" with the quality reason', async () => {
    // same as the z2-sensor-fault scenario: a failed probe sends null with quality FAULT
    server.use(
      http.get("/api/zones/:zoneId", ({ params }) => {
        const telemetry = buildZoneStatus(String(params.zoneId))!;
        if (params.zoneId === "Z2") {
          telemetry.readings.ec = null;
          telemetry.quality.ec = "FAULT";
        }
        return HttpResponse.json(telemetry);
      }),
    );
    renderAt("/");
    const zoneB = await card("Zone B");
    await zoneB.findByText(/^Updated/, undefined, WAIT);
    const ec = zoneB.getByRole("group", { name: "EC" });
    expect(ec).toHaveTextContent("--");
    expect(ec).toHaveTextContent("Sensor fault");
  });

  it("looks Offline, with its last values greyed, when the controller has gone silent", async () => {
    // contract §11.2: an offline zone still answers 200, with online false and quality STALE
    server.use(
      http.get("/api/zones/:zoneId", ({ params }) => {
        const status = buildZoneStatus(String(params.zoneId))!;
        if (params.zoneId === "Z2") {
          status.online = false;
          status.quality = {
            ec: "STALE",
            ph: "STALE",
            solTempC: "STALE",
            reservoirPct: "STALE",
            circFlowLpm: "STALE",
          };
        }
        return HttpResponse.json(status);
      }),
    );
    renderAt("/");
    const zoneB = await card("Zone B");
    expect(await zoneB.findByText("Offline", undefined, WAIT)).toBeInTheDocument();
    const ec = zoneB.getByRole("group", { name: "EC" });
    expect(ec).toHaveClass("reading-off");
    expect(ec).not.toHaveTextContent("--");
    expect(ec).toHaveTextContent("No fresh sample");
  });

  it("shows the readings the real back end (T1-12) sends, with the zone state not known yet", async () => {
    // an answer from GET /api/zones/Z1 on the dev stack with the simulator running: ZONE_STATE isn't
    // stored yet, so mode, estop, leak, stopped and configVersion are null and equipment is {}
    server.use(
      http.get("/api/zones/:zoneId", ({ params }) =>
        HttpResponse.json({
          zone: params.zoneId,
          online: true,
          lastSeen: new Date().toISOString(),
          ts: new Date().toISOString(),
          simTs: "2026-11-03T01:11:05.169Z",
          readings: {
            ec: 1.62,
            ph: 6.04,
            solTempC: 23.4,
            reservoirPct: 78.0,
            reservoirL: 936.0,
            circFlowLpm: 31.0,
            doMgL: null,
            dayTankPct: { A: 64.0, B: 66.0, ACID: 81.0 },
            dayTankL: { A: 32.0, B: 33.0, ACID: 40.5 },
          },
          quality: { ec: "OK", ph: "OK", solTempC: "OK", reservoirPct: "OK", circFlowLpm: "OK" },
          equipment: {},
          mode: null,
          estop: null,
          leak: null,
          stopped: null,
          activeCmdId: null,
          operation: null,
          latches: [],
          configVersion: null,
        }),
      ),
    );
    renderAt("/");
    const zoneA = await card("Zone A");
    expect(await zoneA.findByText(/^Updated/, undefined, WAIT)).toBeInTheDocument();
    expect(zoneA.getByRole("group", { name: "EC" })).toHaveTextContent("1.62");
    expect(zoneA.getByRole("group", { name: "pH" })).toHaveTextContent("6.04");
    expect(zoneA.getByRole("group", { name: "Solution temp" })).toHaveTextContent("23.4");
    expect(zoneA.getByRole("group", { name: "Reservoir level" })).toHaveTextContent("78");
  });

  it("looks Offline with no values for a zone that has never reported", async () => {
    // contract §11.2: still 200; online false, every reading and state field null, quality {}
    server.use(
      http.get("/api/zones/:zoneId", ({ params }) => {
        const status = buildZoneStatus(String(params.zoneId))!;
        if (params.zoneId !== "Z2") return HttpResponse.json(status);
        return HttpResponse.json({
          ...status,
          online: false,
          lastSeen: null,
          ts: null,
          readings: {
            ec: null,
            ph: null,
            solTempC: null,
            reservoirPct: null,
            reservoirL: null,
            circFlowLpm: null,
            doMgL: null,
            dayTankPct: { A: null, B: null, ACID: null },
            dayTankL: { A: null, B: null, ACID: null },
          },
          quality: {},
          equipment: {},
          mode: null,
          estop: null,
          leak: null,
          stopped: null,
          configVersion: null,
        });
      }),
    );
    renderAt("/");
    const zoneB = await card("Zone B");
    expect(await zoneB.findByText("Offline", undefined, WAIT)).toBeInTheDocument();
    expect(zoneB.getByRole("group", { name: "EC" })).toHaveTextContent("--");
    expect(zoneB.getByRole("group", { name: "pH" })).toHaveTextContent("--");
  });

  it("looks Offline when a zone can't be fetched", async () => {
    server.use(
      http.get("/api/zones/:zoneId", () =>
        HttpResponse.json(
          { error: { code: "DEVICE_OFFLINE", message: "Zone controller is not reporting" } },
          { status: 503 },
        ),
      ),
    );
    renderAt("/");
    const zoneA = await card("Zone A");
    expect(await zoneA.findByText("Offline", undefined, WAIT)).toBeInTheDocument();
    expect(zoneA.getByRole("group", { name: "EC" })).toHaveTextContent("--");
    expect(zoneA.getByRole("heading", { name: "Zone A" }).closest("section")).toHaveClass("offline");
  });
});
