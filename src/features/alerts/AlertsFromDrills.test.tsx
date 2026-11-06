// T2-20 · a drill on the Simulator screen shows its alert, and the alert goes on Fix or Release
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { renderAt } from "../../test/render";

// the fake API waits 300 ms per call, so allow more than the 1 s default
const WAIT = { timeout: 3000 };

const card = (title: string) => within(screen.getByRole("heading", { name: title }).closest("section")!);
const strip = () => screen.queryByRole("region", { name: "Alerts" });

async function goTo(user: ReturnType<typeof userEvent.setup>, link: string, heading: string) {
  await user.click(screen.getAllByRole("link", { name: link })[0]);
  await screen.findByRole("heading", { level: 1, name: heading }, WAIT);
}

describe("alerts from the Simulator screen (fake API)", () => {
  it("Leak sensor wet: alert on the Overview until Fix", async () => {
    const user = userEvent.setup();
    renderAt("/simulator", { role: "Operator" });
    await screen.findByRole("heading", { name: "Failure drills" }, WAIT);
    const drills = card("Failure drills");
    await user.selectOptions(drills.getByLabelText("Leak sensor wet: where"), "Zone B");
    await user.click(drills.getByRole("button", { name: "Trigger Leak sensor wet" }));
    await screen.findByRole("list", { name: "Running drills" }, WAIT);

    await goTo(user, "Overview", "Overview");
    expect(await screen.findByText("Leak sensor wet in Zone B.", undefined, WAIT)).toBeInTheDocument();

    await goTo(user, "Simulator", "Simulator");
    await user.click(await screen.findByRole("button", { name: "Fix Leak sensor wet on Zone B" }, WAIT));
    await card("Failure drills").findByText("No failure drills are running.", undefined, WAIT);

    await goTo(user, "Overview", "Overview");
    await expect.poll(strip, WAIT).toBeNull();
  });

  it("a held low reservoir: alert on the zone page until Release", async () => {
    const user = userEvent.setup();
    renderAt("/simulator", { role: "Operator" });
    await screen.findByRole("heading", { name: "Held values" }, WAIT);
    const held = card("Held values");
    await user.selectOptions(held.getByLabelText("Where"), "Zone B");
    await user.selectOptions(held.getByLabelText("Reading"), "Reservoir level");
    await user.type(held.getByLabelText("Value"), "20");
    await user.click(held.getByRole("button", { name: "Hold" }));
    await held.findByRole("list", { name: "Values held now" }, WAIT);

    await goTo(user, "Zone B", "Zone B");
    expect(await screen.findByText("Zone B reservoir is low (20 %).", undefined, WAIT)).toBeInTheDocument();

    await goTo(user, "Simulator", "Simulator");
    await user.click(await screen.findByRole("button", { name: "Release Zone B · Reservoir level" }, WAIT));
    await card("Held values").findByText("No values are being held.", undefined, WAIT);

    await goTo(user, "Zone B", "Zone B");
    await expect.poll(strip, WAIT).toBeNull();
  });
});
