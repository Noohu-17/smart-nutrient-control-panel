import { screen, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { afterEach, describe, expect, it } from "vitest";
import { buildZoneStatus } from "../../mocks/data/telemetry";
import { server } from "../../mocks/node";
import { renderAt } from "../../test/render";

const useScenario = (name: string) => window.sessionStorage.setItem("snm.mockScenario", name);

const card = async (name: string) =>
  within((await screen.findByRole("heading", { name })).closest("section")!);

afterEach(() => window.sessionStorage.clear());

describe("Zone freshness", () => {
  it('says how old the readings are instead of "Live"', async () => {
    renderAt("/");
    const zoneA = await card("Zone A");
    expect(await zoneA.findByText(/^Updated \d+ s ago$/)).toBeInTheDocument();
    expect(zoneA.queryByText("Live")).not.toBeInTheDocument();
  });

  it('shows "Stale" and greys the readings in the z2-stale scenario', async () => {
    useScenario("z2-stale");
    renderAt("/");
    const zoneB = await card("Zone B");
    expect(await zoneB.findByText("Stale")).toBeInTheDocument();
    expect(zoneB.getByRole("group", { name: "EC" })).toHaveClass("reading-off");
  });

  it("shows Stale when the back end marks a reading STALE", async () => {
    server.use(
      http.get("/api/zones/:zoneId", ({ params }) => {
        const zone = buildZoneStatus(String(params.zoneId))!;
        return HttpResponse.json(
          params.zoneId === "Z2" ? { ...zone, quality: { ...zone.quality, ec: "STALE" } } : zone,
        );
      }),
    );
    renderAt("/");
    expect(await (await card("Zone A")).findByText(/^Updated \d+ s ago$/)).toBeInTheDocument();
    expect(await (await card("Zone B")).findByText("Stale")).toBeInTheDocument();
  });

  it('says "Loading", not "No data", until the first answer', async () => {
    server.use(http.get("/api/zones/:zoneId", () => new Promise<Response>(() => {})));
    renderAt("/");
    const ec = (await card("Zone A")).getByRole("group", { name: "EC" });
    expect(ec).toHaveTextContent("Loading");
    expect(ec).not.toHaveTextContent("No data");
  });
});

const strip = async () => within(await screen.findByRole("region", { name: "Alerts" }));

describe("Alert strip (T2-20)", () => {
  it("E-STOP: at the top of the Overview, with a link to the zone, and not again on the card", async () => {
    useScenario("z1-estop");
    renderAt("/");
    const alert = (await strip()).getByRole("alert");
    expect(alert).toHaveTextContent("E-STOP pressed in Zone A.");
    expect(alert).toHaveTextContent("press Resume");
    expect(within(alert).getByRole("link", { name: "Open Zone A" })).toHaveAttribute("href", "/zone/Z1");
    expect((await card("Zone A")).queryByRole("alert")).not.toBeInTheDocument();
  });

  it("E-STOP on the Zone page: one alert, with Resume locked until it's released", async () => {
    useScenario("z1-estop");
    renderAt("/zone/Z1");
    const alerts = (await strip()).getAllByRole("alert");
    expect(alerts).toHaveLength(1);
    expect(alerts[0]).toHaveTextContent("E-STOP pressed in Zone A.");
    expect(within(alerts[0]).getByRole("button", { name: "Resume" })).toBeDisabled();
    expect(screen.queryByText("Zone A is stopped.")).not.toBeInTheDocument();
  });

  it.each([
    ["z1-leak", "/zone/Z1", "Leak sensor wet in Zone A."],
    ["z2-sensor-fault", "/", "EC sensor fault in Zone B."],
    ["z1-ec-stuck", "/zone/Z1", "EC reading is not updating in Zone A."],
    ["z2-stale", "/", "No fresh readings from Zone B."],
    ["z2-reservoir-low", "/zone/Z2", "Zone B reservoir is low (22 %)."],
    ["z2-offline", "/", "Zone B controller is offline."],
  ])("%s on %s: %s", async (scenario, path, title) => {
    useScenario(scenario);
    renderAt(path);
    expect(await (await strip()).findByText(title)).toBeInTheDocument();
  });

  it("puts critical first across zones", async () => {
    server.use(
      http.get("/api/zones/:zoneId", ({ params }) => {
        const zone = buildZoneStatus(String(params.zoneId))!;
        if (params.zoneId === "Z1") zone.readings.reservoirPct = 10;
        if (params.zoneId === "Z2") zone.leak = true;
        return HttpResponse.json(zone);
      }),
    );
    renderAt("/");
    const s = await strip();
    await s.findByText("Zone A reservoir is low (10 %).");
    await s.findByText("Leak sensor wet in Zone B.");
    expect(s.getAllByRole("alert").map((a) => a.querySelector("b")!.textContent)).toEqual([
      "Leak sensor wet in Zone B.",
      "Zone A reservoir is low (10 %).",
    ]);
  });

  it("shows no strip when every zone is healthy", async () => {
    renderAt("/");
    await (await card("Zone A")).findByText(/^Updated/);
    await (await card("Zone B")).findByText(/^Updated/);
    expect(screen.queryByRole("region", { name: "Alerts" })).not.toBeInTheDocument();
  });
});
