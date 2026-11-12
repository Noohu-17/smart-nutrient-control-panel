import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { renderAt } from "../../test/render";

describe("Dose on an Overview zone card", () => {
  it("opens that zone's page at its dose panel", async () => {
    const user = userEvent.setup();
    renderAt("/", { role: "Operator" });
    const zoneA = within((await screen.findByRole("heading", { name: "Zone A" })).closest("section")!);
    await user.click(zoneA.getByRole("button", { name: "Dose" }));
    expect(await screen.findByRole("heading", { level: 1, name: "Zone A" })).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "Manual dose" })).toBeInTheDocument();
  });
});
