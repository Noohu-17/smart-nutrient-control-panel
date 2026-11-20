import { screen, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import clientBFeatures from "../../../../config/clients/client_B/features.json";
import clientBLayout from "../../../../config/clients/client_B/layout.json";
import type { Farm, ServerAlert } from "../../api/types";
import { farmLayout } from "../../mocks/data/layout";
import { server } from "../../mocks/node";
import { renderAt } from "../../test/render";

/* Live and simulated zones (contract §2.4, T1-33): the label on zone cards and the zone page,
   no simulator controls for a live zone, and real times for it. */

const serveFarm = (farm: Farm) => server.use(http.get("/api/farm", () => HttpResponse.json(farm)));

/** client_B as GET /farm answers it: a real farm, both zones LIVE, no playground. */
const clientB = { ...clientBLayout, features: clientBFeatures } as unknown as Farm;

/** client_A's playground with Zone B on real hardware. */
const zoneBLive: Farm = {
  ...farmLayout,
  zones: farmLayout.zones.map((z) => (z.id === "Z2" ? { ...z, source: "LIVE" } : z)),
};

const card = async (name: string) =>
  within((await screen.findByRole("heading", { level: 2, name })).closest("section")!);

describe("live and simulated zones (T1-33)", () => {
  it("labels client_A's zones Simulated on their cards", async () => {
    renderAt("/");
    expect((await card("Zone A")).getByText("Simulated")).toBeInTheDocument();
    expect((await card("Zone B")).getByText("Simulated")).toBeInTheDocument();
  });

  it("labels client_B's zones Live, with no simulator anywhere", async () => {
    serveFarm(clientB);
    renderAt("/");
    expect((await card("NFT Zone A")).getByText("Live")).toBeInTheDocument();
    expect((await card("NFT Zone B")).getByText("Live")).toBeInTheDocument();
    expect(screen.queryByText("Simulated")).not.toBeInTheDocument();
    const menu = within(screen.getByRole("navigation", { name: "Main" }));
    expect(menu.queryByRole("link", { name: "Simulator" })).not.toBeInTheDocument();
  });

  it("labels client_B's zone page Live, without the playground note", async () => {
    serveFarm(clientB);
    renderAt("/zone/Z1");
    const title = await screen.findByRole("heading", { level: 1, name: "NFT Zone A" });
    expect(within(title.parentElement!).getByText("Live")).toBeInTheDocument();
    expect(screen.queryByText(/The simulator doesn’t change this zone/)).not.toBeInTheDocument();
  });

  it("gives client_B no simulator controls, even from a typed URL", async () => {
    serveFarm(clientB);
    renderAt("/simulator");
    expect(await screen.findByRole("heading", { name: "Simulator not available" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Held values" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /reset|trigger|hold/i })).not.toBeInTheDocument();
  });

  it("says a live zone on a playground site runs on real time", async () => {
    serveFarm(zoneBLive);
    renderAt("/zone/Z2");
    const title = await screen.findByRole("heading", { level: 1, name: "Zone B" });
    expect(within(title.parentElement!).getByText("Live")).toBeInTheDocument();
    expect(screen.getByText(/Real hardware, on real time/)).toBeInTheDocument();
  });

  it("leaves a live zone out of the simulator's controls, and has no Reset", async () => {
    serveFarm(zoneBLive);
    renderAt("/simulator");
    await screen.findByRole("heading", { name: "Held values" });
    expect(screen.getByText(/Zone B is live: real hardware, on real time/)).toBeInTheDocument();

    const where = screen.getByLabelText("Where");
    const options = within(where)
      .getAllByRole("option")
      .map((o) => o.textContent);
    expect(options).toEqual(["Zone A", "Polyhouse"]);

    // every drill's place picker and the E-STOP card offer Zone A's parts only
    for (const select of screen.getAllByRole("combobox"))
      for (const option of within(select).queryAllByRole("option"))
        expect(option.textContent).not.toMatch(/Zone B|ZC2/);
    const estop = within(screen.getByRole("heading", { name: "E-STOP and power" }).closest("section")!);
    expect(estop.queryByRole("button", { name: /on Zone B|on ZC2/ })).not.toBeInTheDocument();
    expect(estop.getByRole("button", { name: "Press E-STOP on Zone A" })).toBeInTheDocument();

    expect(screen.queryByRole("button", { name: "Reset" })).not.toBeInTheDocument();
    expect(
      screen.getByText("Reset is off while a zone is live: its totals and alerts are real."),
    ).toBeInTheDocument();
  });

  it("shows a live zone's alert at the real time only, with no farm clock", async () => {
    const alert = (id: string, zone: string): ServerAlert => ({
      id,
      kind: "EC_HIGH",
      severity: "CRITICAL",
      status: "OPEN",
      zone,
      message: `EC high in ${zone}`,
      systemAction: "No automatic action",
      nextStep: "Dilute with fresh water",
      raisedAt: "2026-11-12T05:10:04.000Z",
      simTs: "2026-11-12T19:30:00.000Z",
      clearedAt: null,
      acknowledgedAt: null,
      acknowledgedBy: null,
    });
    serveFarm(zoneBLive);
    server.use(
      http.get("/api/alerts", () => HttpResponse.json({ items: [alert("a1", "Z1"), alert("a2", "Z2")] })),
    );
    renderAt("/alerts");
    const list = within(await screen.findByRole("list", { name: "Current alerts" }));
    const [simulated, live] = list.getAllByRole("listitem");
    expect(simulated).toHaveTextContent(/farm clock/);
    expect(live).toHaveTextContent(/Raised/);
    expect(live).not.toHaveTextContent(/farm clock/);
  });
});
