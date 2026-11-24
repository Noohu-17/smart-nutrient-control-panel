// Playground controls on the Simulator screen (contract §11.4) against the fake API
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import configScenarios from "../../../../config/clients/client_A/scenarios.json";
import type { Role } from "../../auth/roles";
import { farmLayout } from "../../mocks/data/layout";
import { server } from "../../mocks/node";
import { SWITCH_DRILLS } from "../../config/sim";
import { scenarioOptions, simState } from "../../mocks/sim";
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
    expect(banner()).toHaveTextContent(
      /^Simulated farm · Farm clock: .+ · Running 60× faster \(1 real minute = 1 farm hour\)$/,
    );

    await user.click(clock.getByRole("button", { name: "×288" }));
    expect(await within(banner()).findByText(/Running 288× faster/)).toBeInTheDocument();
    expect(clock.getByRole("button", { name: "×288" })).toHaveAttribute("aria-pressed", "true");

    await user.click(clock.getByRole("button", { name: "Pause" }));
    expect(await within(banner()).findByText(/Paused/)).toBeInTheDocument();
    expect(clock.getByRole("button", { name: "×1" })).toHaveAttribute("aria-pressed", "false");
  });

  it("lists every failure drill from scenario-options, with its explanation", async () => {
    await open();
    const drills = within(screen.getByRole("list", { name: "Drills you can trigger" }));
    const switches = card("E-STOP and power");
    for (const drill of configScenarios.failureDrills) {
      // the E-STOP and the power cut have their own card, not a Trigger row
      const where = drill.kind in SWITCH_DRILLS ? switches : drills;
      expect(where.getAllByText(drill.title).length).toBeGreaterThan(0);
      expect(where.getByText(drill.explanation)).toBeInTheDocument();
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
    // The entry stays and a line confirms it; holding the same reading again updates it.
    expect(held.getByLabelText("Value")).toHaveValue(7.2);
    // The line comes with the hold's answer, which may arrive just after the list refreshes.
    await waitFor(() => expect(held.getByRole("status")).toHaveTextContent("Held: Zone A · pH at 7.2."));
    await user.clear(held.getByLabelText("Value"));
    expect(held.getByRole("status")).toHaveTextContent("");
    await user.type(held.getByLabelText("Value"), "6.5");
    await user.click(held.getByRole("button", { name: "Update hold" }));
    expect(await list.findByText("Held at 6.5")).toBeInTheDocument();
    expect(list.queryByText("Held at 7.2")).not.toBeInTheDocument();

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
      await user.clear(held.getByLabelText("Value"));
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
    const ask = within(card("Clock").getByRole("group", { name: "Reset the farm to the starting state?" }));
    await user.click(ask.getByRole("button", { name: "Yes, reset" }));
    expect(await card("Failure drills").findByText("No failure drills are running.")).toBeInTheDocument();
    expect(card("Clock").getByRole("button", { name: "×60" })).toHaveAttribute("aria-pressed", "true");
  });

  it("asks before Reset, and Cancel sends nothing", async () => {
    let resets = 0;
    server.use(
      http.post("/api/sim/reset", () => {
        resets += 1;
        return HttpResponse.json(simState());
      }),
    );
    const user = await open({ role: "Owner" });
    await user.click(card("Clock").getByRole("button", { name: "Reset" }));
    expect(card("Clock").getByText("Reset the farm to the starting state?")).toBeInTheDocument();
    await user.click(card("Clock").getByRole("button", { name: "Cancel" }));
    expect(card("Clock").queryByText("Reset the farm to the starting state?")).not.toBeInTheDocument();
    expect(card("Clock").getByRole("button", { name: "Reset" })).toBeEnabled();
    expect(resets).toBe(0);
  });

  it("presses and releases the E-STOP on one zone", async () => {
    const user = await open();
    const estop = within(screen.getByRole("list", { name: "E-STOP pressed" }));
    await user.click(estop.getByRole("button", { name: "Press E-STOP on Zone B" }));

    expect(await estop.findByRole("button", { name: "Release E-STOP on Zone B" })).toBeInTheDocument();
    expect(estop.getByText("E-STOP pressed")).toBeInTheDocument();
    expect(estop.getByRole("button", { name: "Press E-STOP on Zone A" })).toBeInTheDocument();
    // its own card, so it isn't listed again under Failure drills
    expect(card("Failure drills").getByText("No failure drills are running.")).toBeInTheDocument();

    await user.click(estop.getByRole("button", { name: "Release E-STOP on Zone B" }));
    expect(await estop.findByRole("button", { name: "Press E-STOP on Zone B" })).toBeInTheDocument();
    expect(estop.queryByText("E-STOP pressed")).not.toBeInTheDocument();
  });

  it("offers a power cut per device when scenarios.json lists it", async () => {
    const user = await open();
    const power = within(screen.getByRole("list", { name: "Power cut" }));
    await user.click(power.getByRole("button", { name: "Cut power on ZC1" }));
    expect(await power.findByRole("button", { name: "Restore power on ZC1" })).toBeInTheDocument();
  });

  it("has no E-STOP and power card when scenarios.json lists neither", async () => {
    server.use(
      http.get("/api/sim/scenario-options", () =>
        HttpResponse.json({
          ...scenarioOptions,
          failureDrills: scenarioOptions.failureDrills.filter((d) => !(d.kind in SWITCH_DRILLS)),
        }),
      ),
    );
    await open();
    expect(screen.queryByRole("heading", { name: "E-STOP and power" })).not.toBeInTheDocument();
  });

  it("shows the server's message when the simulator refuses an action", async () => {
    // what the real back end passes on from the simulator (T1-20, §11.1)
    const refuse = (message: string) =>
      HttpResponse.json({ error: { code: "VALIDATION_ERROR", message } }, { status: 422 });
    server.use(
      http.post("/api/sim/faults", () => refuse("Power cut isn't built in the simulator yet")),
      http.post("/api/sim/speed", () => refuse("Speed must be one of 0, 1, 60, 288")),
      http.post("/api/sim/holds", () => refuse("ph must be a number from 4 to 9")),
      http.post("/api/sim/reset", () =>
        HttpResponse.json(
          { error: { code: "SERVICE_UNAVAILABLE", message: "The simulator is not available right now" } },
          { status: 503 },
        ),
      ),
    );
    const user = await open({ role: "Owner" });

    const switches = card("E-STOP and power");
    await user.click(switches.getByRole("button", { name: "Cut power on ZC1" }));
    expect(await switches.findByText("Power cut isn't built in the simulator yet")).toBeInTheDocument();
    expect(switches.getByRole("button", { name: "Cut power on ZC1" })).toBeEnabled();

    await user.click(card("Clock").getByRole("button", { name: "×288" }));
    expect(await card("Clock").findByText("Speed must be one of 0, 1, 60, 288")).toBeInTheDocument();
    expect(card("Clock").getByRole("button", { name: "×60" })).toHaveAttribute("aria-pressed", "true");

    await user.click(card("Clock").getByRole("button", { name: "Reset" }));
    await user.click(card("Clock").getByRole("button", { name: "Yes, reset" }));
    expect(await card("Clock").findByText("The simulator is not available right now")).toBeInTheDocument();

    const held = card("Held values");
    await user.selectOptions(held.getByLabelText("Reading"), "pH");
    await user.type(held.getByLabelText("Value"), "7");
    await user.click(held.getByRole("button", { name: "Hold" }));
    expect(await held.findByText("ph must be a number from 4 to 9")).toBeInTheDocument();
  });

  it("hides the screen and the banner when the back end has no simulator (404)", async () => {
    const none = () =>
      HttpResponse.json(
        { error: { code: "NOT_FOUND", message: "This site has no simulator" } },
        { status: 404 },
      );
    server.use(http.get("/api/sim/state", none), http.get("/api/sim/scenario-options", none));
    renderAt("/simulator");
    expect(await screen.findByRole("heading", { name: "Simulator not available" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Clock" })).not.toBeInTheDocument();
    expect(screen.queryByText("Simulated farm")).not.toBeInTheDocument();
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
    await within(banner()).findByText(/Running 60× faster/);
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
