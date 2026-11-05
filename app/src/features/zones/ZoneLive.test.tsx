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

describe("Safety banners", () => {
  it("shows the E-STOP banner on the zone card in the z1-estop scenario", async () => {
    useScenario("z1-estop");
    renderAt("/");
    const alert = await (await card("Zone A")).findByRole("alert");
    expect(alert).toHaveTextContent("E-STOP pressed in Zone A.");
    expect(alert).toHaveTextContent("press Resume");
  });

  it("shows the E-STOP banner on the Zone page", async () => {
    useScenario("z1-estop");
    renderAt("/zone/Z1");
    expect(await screen.findByRole("alert")).toHaveTextContent("E-STOP pressed in Zone A.");
  });

  it("shows the Leak banner on the Zone page in the z1-leak scenario", async () => {
    useScenario("z1-leak");
    renderAt("/zone/Z1");
    expect(await screen.findByRole("alert")).toHaveTextContent("Leak sensor wet in Zone A.");
  });

  it("shows no banner on a healthy zone", async () => {
    renderAt("/");
    const zoneA = await card("Zone A");
    await zoneA.findByText(/^Updated/);
    expect(zoneA.queryByRole("alert")).not.toBeInTheDocument();
  });
});
