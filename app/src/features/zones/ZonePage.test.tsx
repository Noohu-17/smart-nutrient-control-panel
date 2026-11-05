import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { renderAt } from "../../test/render";

describe("Zone page", () => {
  it("shows Zone A's name from GET /api/farm", async () => {
    renderAt("/zone/Z1");
    expect(await screen.findByRole("heading", { level: 1, name: "Zone A" })).toBeInTheDocument();
  });

  it("lists the zone's benches", async () => {
    renderAt("/zone/Z1");
    const benches = within((await screen.findByRole("heading", { name: "Benches" })).closest("section")!);
    expect(benches.getAllByRole("listitem").map((li) => li.querySelector("b")?.textContent)).toEqual([
      "Z1-B1",
      "Z1-B2",
      "Z1-B3",
      "Z1-B4",
    ]);
  });

  it("shows live EC, pH, solution temp and reservoir level", async () => {
    renderAt("/zone/Z1");
    const readings = within((await screen.findByRole("heading", { name: "Readings" })).closest("section")!);
    await readings.findByText(/^Updated/);
    for (const label of ["EC", "pH", "Solution temp", "Reservoir level"]) {
      expect(readings.getByRole("group", { name: label })).not.toHaveTextContent("--");
    }
  });

  it("uses the other zone's own name and benches", async () => {
    renderAt("/zone/Z2");
    expect(await screen.findByRole("heading", { level: 1, name: "Zone B" })).toBeInTheDocument();
    expect(screen.getByText("Z2-B1")).toBeInTheDocument();
  });

  it('shows "Zone not found" for an unknown zone ID', async () => {
    renderAt("/zone/Z9");
    expect(await screen.findByRole("heading", { name: "Zone not found" })).toBeInTheDocument();
    expect(screen.getByText(/There is no zone called "Z9"/)).toBeInTheDocument();
  });
});
