// T2-15 done-when: the Climate page shows fake data
import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { renderAt } from "../../test/render";

describe("Climate page on the fake API", () => {
  it("shows air inside and outside, each sensor position and the electrical room", async () => {
    renderAt("/climate");
    const air = await screen.findByRole("group", { name: "Air temp" });
    expect(air).toHaveTextContent("°C");
    expect(air).not.toHaveTextContent("--");
    for (const name of ["Humidity", "Outside temp", "Outside humidity", "Electrical room"])
      expect(screen.getByRole("group", { name })).toBeInTheDocument();
    // positions come from layout.climate.airSensors
    for (const name of ["Pad end", "Middle", "Fan end"])
      expect(await screen.findByRole("group", { name })).toBeInTheDocument();
  });

  it("says who plays the climate: Simulated, or Live on a real farm", async () => {
    renderAt("/climate");
    const title = (await screen.findByRole("heading", { level: 1, name: "Climate" })).parentElement!;
    expect(await within(title).findByText("Simulated")).toBeInTheDocument();
  });

  it.each(["client-b", "no-playground"] as const)("labels the climate on the %s scenario", async (mock) => {
    window.history.replaceState(null, "", `/climate?mock=${mock}`);
    renderAt("/climate");
    const title = (await screen.findByRole("heading", { level: 1, name: "Climate" })).parentElement!;
    expect(await within(title).findByText(mock === "client-b" ? "Simulated" : "Live")).toBeInTheDocument();
    window.history.replaceState(null, "", "/");
    window.sessionStorage.clear();
  });

  it("shows every value as old while the climate controller is offline", async () => {
    window.history.replaceState(null, "", "/climate?mock=climate-offline");
    renderAt("/climate");
    for (const name of ["Air temp", "Humidity", "Electrical room", "Pad end", "Fan end"])
      expect(await screen.findByRole("group", { name })).toHaveTextContent("No fresh sample");
    window.history.replaceState(null, "", "/");
    window.sessionStorage.clear();
  });
});
