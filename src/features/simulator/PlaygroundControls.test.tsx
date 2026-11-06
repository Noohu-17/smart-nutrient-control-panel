// Playground controls on the Simulator screen (contract §11.4) against the fake API
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import configScenarios from "../../../config/clients/client_A/scenarios.json";
import type { Role } from "../../auth/roles";
import { farmLayout } from "../../mocks/data/layout";
import { server } from "../../mocks/node";
import { scenarioOptions } from "../../mocks/sim";
import { renderAt } from "../../test/render";

async function open({ role = "Operator" as Role } = {}) {
  const user = userEvent.setup();
  renderAt("/simulator", { role });
  await screen.findByRole("heading", { name: "Failure drills" });
  return user;
}

const card = (title: string) => within(screen.getByRole("heading", { name: title }).closest("section")!);
const banner = () => screen.getByText("Simulated farm").closest<HTMLElement>(".sim-banner")!;

describe("Simulator screen: playground controls", () => {
  it("changes the speed, and the banner says the new speed", async () => {
    const user = await open();
    const clock = card("Clock");
    expect(clock.getByRole("button", { name: "×60" })).toHaveAttribute("aria-pressed", "true");
    expect(banner()).toHaveTextContent(/^Simulated farm · ×60 · /);

    await user.click(clock.getByRole("button", { name: "×288" }));
    expect(await within(banner()).findByText(/×288/)).toBeInTheDocument();
    expect(clock.getByRole("button", { name: "×288" })).toHaveAttribute("aria-pressed", "true");

    await user.click(clock.getByRole("button", { name: "Pause" }));
    expect(await within(banner()).findByText(/Paused/)).toBeInTheDocument();
    expect(clock.getByRole("button", { name: "×1" })).toHaveAttribute("aria-pressed", "false");
  });

  it("lists every failure drill from scenario-options, with its explanation", async () => {
    await open();
    const drills = within(screen.getByRole("list", { name: "Drills you can trigger" }));
    for (const drill of configScenarios.failureDrills) {
      expect(drills.getByText(drill.title)).toBeInTheDocument();
      expect(drills.getByText(drill.explanation)).toBeInTheDocument();
    }
  });

  it("builds the drill list from the API, not from code", async () => {
    server.use(
      http.get("/api/sim/scenario-options", () =>
        HttpResponse.json({
          ...scenarioOptions,
          failureDrills: [
            {
              kind: "FAN_FAILURE",
              group: "CLIMATE_AND_ROOMS",
              prdCode: "F21",
              targetType: "POLYHOUSE",
              title: "Fan stops",
              explanation: "An exhaust fan stops turning.",
              fix: "Restarts the fan.",
              params: {},
            },
          ],
        }),
      ),
    );
    await open();
    const drills = within(screen.getByRole("list", { name: "Drills you can trigger" }));
    expect(drills.getByText("Fan stops")).toBeInTheDocument();
    expect(drills.queryByText("Leak sensor wet")).not.toBeInTheDocument();
  });

  it("triggers a drill on the chosen zone, marks it running, and fixes it", async () => {
    const user = await open();
    const drills = card("Failure drills");
    await user.selectOptions(drills.getByLabelText("Leak sensor wet: where"), "Zone B");
    await user.click(drills.getByRole("button", { name: "Trigger Leak sensor wet" }));

    const running = within(await screen.findByRole("list", { name: "Running drills" }));
    expect(running.getByText("Leak sensor wet · Zone B")).toBeInTheDocument();
    expect(running.getByText("Fix: Dries the leak sensor.")).toBeInTheDocument();
    expect(drills.getByText("Running: Zone B")).toBeInTheDocument();
    expect(drills.getByRole("button", { name: "Running Leak sensor wet" })).toBeDisabled();

    await user.click(running.getByRole("button", { name: "Fix Leak sensor wet on Zone B" }));
    expect(await drills.findByText("No failure drills are running.")).toBeInTheDocument();
    expect(drills.queryByText("Running: Zone B")).not.toBeInTheDocument();
  });

  it("holds a value inside the allowed range and releases it", async () => {
    const user = await open();
    const held = card("Held values");
    await user.selectOptions(held.getByLabelText("Where"), "Zone A");
    await user.selectOptions(held.getByLabelText("Reading"), "pH");
    expect(held.getByText("Between 0 and 14")).toBeInTheDocument();

    await user.type(held.getByLabelText("Value"), "15");
    expect(held.getByRole("button", { name: "Hold" })).toBeDisabled();
    await user.clear(held.getByLabelText("Value"));
    await user.type(held.getByLabelText("Value"), "7.2");
    await user.click(held.getByRole("button", { name: "Hold" }));

    const list = within(await held.findByRole("list", { name: "Values held now" }));
    expect(list.getByText("Zone A · pH")).toBeInTheDocument();
    expect(list.getByText("Held at 7.2")).toBeInTheDocument();

    await user.click(list.getByRole("button", { name: "Release Zone A · pH" }));
    expect(await held.findByText("No values are being held.")).toBeInTheDocument();
  });

  it("offers the polyhouse air and each zone's day-tanks, with their ranges", async () => {
    const user = await open();
    const held = card("Held values");
    const reading = held.getByLabelText("Reading");
    expect(within(reading).getByRole("option", { name: "Day-tank Acid" })).toBeInTheDocument();

    await user.selectOptions(held.getByLabelText("Where"), "Polyhouse");
    expect(
      within(reading)
        .getAllByRole("option")
        .map((o) => o.textContent),
    ).toEqual(["Air temp", "Humidity"]);
    expect(held.getByText("Between 0 and 50 °C")).toBeInTheDocument();
  });

  it("Release all clears every held value", async () => {
    const user = await open();
    const held = card("Held values");
    for (const value of ["2.5", "3"]) {
      await user.selectOptions(held.getByLabelText("Reading"), value === "3" ? "EC" : "Solution temp");
      await user.type(held.getByLabelText("Value"), value);
      await user.click(held.getByRole("button", { name: "Hold" }));
      await held.findByText(`Held at ${value}`, { exact: false });
    }
    await user.click(held.getByRole("button", { name: "Release all" }));
    expect(await held.findByText("No values are being held.")).toBeInTheDocument();
  });

  it("Reset takes the Owner back to ×60 with drills off and values released", async () => {
    const user = await open({ role: "Owner" });
    await user.click(card("Clock").getByRole("button", { name: "×288" }));
    await user.click(card("Failure drills").getByRole("button", { name: "Trigger Heatwave" }));
    await screen.findByRole("list", { name: "Running drills" });

    await user.click(card("Clock").getByRole("button", { name: "Reset" }));
    expect(await card("Failure drills").findByText("No failure drills are running.")).toBeInTheDocument();
    expect(card("Clock").getByRole("button", { name: "×60" })).toHaveAttribute("aria-pressed", "true");
  });

  it("locks Reset for everyone but the Owner, with the reason", async () => {
    await open({ role: "Agronomist" });
    const clock = card("Clock");
    expect(clock.getByRole("button", { name: "Reset" })).toBeDisabled();
    expect(clock.getByRole("button", { name: "Reset" })).toHaveAccessibleDescription(
      "Only Owners reset the simulated farm",
    );
    expect(clock.getByRole("button", { name: "×288" })).toBeEnabled();
  });

  it("shows a Viewer every control, locked with the reason", async () => {
    await open({ role: "Viewer" });
    const main = within(screen.getByRole("main"));
    const buttons = main.getAllByRole("button");
    expect(buttons.length).toBeGreaterThan(5);
    for (const b of buttons) expect(b).toBeDisabled();
    expect(card("Clock").getByRole("button", { name: "×1" })).toHaveAccessibleDescription(
      "Viewers can’t change the simulated farm",
    );
    expect(card("Failure drills").getByText("Viewers can’t change the simulated farm")).toBeInTheDocument();
    expect(card("Held values").getByLabelText("Value")).toBeDisabled();
    expect(card("Held values").getByLabelText("Where")).toBeDisabled();
  });

  it("puts the Simulated farm banner on every screen of a playground site", async () => {
    renderAt("/");
    expect(await screen.findByText("Simulated farm")).toBeInTheDocument();
    await within(banner()).findByText(/×60/);
  });

  it("has no Simulated farm banner on a real farm", async () => {
    server.use(
      http.get("/api/farm", () =>
        HttpResponse.json({ ...farmLayout, features: { ...farmLayout.features, playground: false } }),
      ),
    );
    renderAt("/");
    await screen.findAllByRole("link", { name: "Zone A" });
    expect(screen.queryByText("Simulated farm")).not.toBeInTheDocument();
  });

  it("says so when the simulator can't be reached", async () => {
    server.use(
      http.get("/api/sim/state", () =>
        HttpResponse.json({ error: { code: "INTERNAL", message: "Simulator down" } }, { status: 500 }),
      ),
    );
    renderAt("/simulator");
    expect(await screen.findByText("Could not reach the simulator: Simulator down")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Farm view" })).toBeInTheDocument();
  });
});
