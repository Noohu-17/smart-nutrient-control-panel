import { screen, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { buildZoneTelemetry } from "../../mocks/data/telemetry";
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
    expect(await zoneA.findByText("Live", undefined, WAIT)).toBeInTheDocument();
    // the fake readings drift a little on every request, so check near the Z1 seed and the decimals shown
    for (const [label, seed, decimals] of [
      ["EC", 1.62, 2],
      ["pH", 6.04, 2],
      ["Solution temp", 23.4, 1],
      ["Reservoir level", 78, 0],
    ] as const) {
      const shown = zoneA.getByText(label).closest(".tile")!.querySelector("b")!.textContent!;
      expect(shown.split(".")[1]?.length ?? 0).toBe(decimals);
      expect(Number(shown)).toBeCloseTo(seed, 0);
    }
  });

  it('shows a null reading as "--" with the quality reason', async () => {
    // same as the z2-sensor-fault scenario: a failed probe sends null with quality FAULT
    server.use(
      http.get("/api/zones/:zoneId", ({ params }) => {
        const telemetry = buildZoneTelemetry(String(params.zoneId))!;
        if (params.zoneId === "Z2") {
          telemetry.readings.ec = null;
          telemetry.quality.ec = "FAULT";
        }
        return HttpResponse.json(telemetry);
      }),
    );
    renderAt("/");
    const zoneB = await card("Zone B");
    await zoneB.findByText("Live", undefined, WAIT);
    const ec = zoneB.getByText("EC").closest(".tile")!;
    expect(ec).toHaveTextContent("--");
    expect(ec).toHaveTextContent("Sensor fault");
  });

  it("looks Offline when a zone has no data", async () => {
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
    expect(zoneA.getByText("EC").closest(".tile")).toHaveTextContent("--");
    expect(zoneA.getByRole("heading", { name: "Zone A" }).closest("section")).toHaveClass("offline");
  });
});
