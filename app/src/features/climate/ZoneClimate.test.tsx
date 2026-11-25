// Climate card on the zone page: the house climate, the same for every zone, with everything the
// old Climate page had (air inside and outside, each sensor position, the electrical room, controls)
import { screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { vpdKpa } from "../../config/readings";
import { renderAt } from "../../test/render";

const climateCard = async () =>
  within((await screen.findByRole("heading", { name: "House climate" })).closest("section")!);

function openWithScenario(path: string, scenario: string) {
  window.history.replaceState(null, "", `${path}?mock=${scenario}`);
}

afterEach(() => {
  window.history.replaceState(null, "", "/");
  window.sessionStorage.clear();
});

describe("vpdKpa", () => {
  it("works VPD out from air temp and RH", () => {
    expect(vpdKpa(31.2, 71)).toBe(1.32);
    expect(vpdKpa(25, 100)).toBe(0);
    expect(vpdKpa(null, 71)).toBeNull();
    expect(vpdKpa(31.2, undefined)).toBeNull();
  });
});

describe("Climate card on the zone page", () => {
  it.each(["Z1", "Z2"])(
    "shows the air inside and outside, VPD, each position, the electrical room and fan stage on %s",
    async (zoneId) => {
      renderAt(`/zone/${zoneId}`);
      const card = await climateCard();
      for (const name of ["Air temp", "Humidity", "Outside temp", "Outside humidity", "VPD"]) {
        const reading = await card.findByRole("group", { name });
        await within(reading).findByText(/^\d/);
      }
      expect(card.getByRole("group", { name: "VPD" })).toHaveTextContent("kPa");
      expect(await card.findByText("2 of 4")).toBeInTheDocument(); // fanStage of the layout's four fans
      expect(card.getByText("Simulated")).toBeInTheDocument();
      // positions come from layout.climate.airSensors
      expect(card.getByRole("heading", { name: "Air temp along the house" })).toBeInTheDocument();
      for (const name of ["Pad end", "Middle", "Fan end", "Electrical room"])
        expect(card.getByRole("group", { name })).toBeInTheDocument();
      expect(card.queryByRole("link", { name: "Climate page" })).not.toBeInTheDocument();
    },
  );

  it("has the climate controls", async () => {
    renderAt("/zone/Z1");
    expect((await climateCard()).getByRole("button", { name: "Control equipment" })).toBeEnabled();
  });

  it("shows the values as old while the climate controller is offline", async () => {
    openWithScenario("/zone/Z1", "climate-offline");
    renderAt("/zone/Z1");
    const card = await climateCard();
    for (const name of ["Air temp", "Humidity", "VPD", "Electrical room", "Pad end"])
      await waitFor(() => expect(card.getByRole("group", { name })).toHaveTextContent("No fresh sample"));
  });

  it("is labelled Live on a real farm", async () => {
    openWithScenario("/zone/Z1", "no-playground");
    renderAt("/zone/Z1");
    const card = await climateCard();
    expect(card.getByText("Live")).toBeInTheDocument();
    expect(card.queryByText("Simulated")).not.toBeInTheDocument();
  });

  it("is labelled Simulated on a real farm whose climate the simulator plays (client_B)", async () => {
    openWithScenario("/zone/Z1", "client-b");
    renderAt("/zone/Z1");
    const card = await climateCard();
    expect(await card.findByText("Simulated")).toBeInTheDocument();
    expect(card.queryByText("Live")).not.toBeInTheDocument();
    await within(card.getByRole("group", { name: "Air temp" })).findByText(/^\d/);
    expect(card.queryByText("Fan stage")).not.toBeInTheDocument(); // no fans in its layout
  });

  it("is hidden on a site with no climate controller", async () => {
    openWithScenario("/zone/Z1", "no-climate");
    renderAt("/zone/Z1");
    await screen.findByRole("heading", { name: "Readings" });
    expect(screen.queryByRole("heading", { name: "House climate" })).not.toBeInTheDocument();
  });
});

describe("Climate line on the zone cards", () => {
  it("shows a short climate line on each Overview zone card", async () => {
    renderAt("/");
    await screen.findByRole("heading", { name: "Zone A" });
    const line = /°C · \d+ % · VPD \d+\.\d{2} kPa/;
    await waitFor(() => expect(screen.getAllByText(line)).toHaveLength(2)); // one per zone
    const lines = screen.getAllByText(line);
    expect(within(lines[0]).getByText("House climate")).toBeInTheDocument(); // the full climate is on the zone page
  });

  it("is left out on a site with no climate controller", async () => {
    openWithScenario("/", "no-climate");
    renderAt("/");
    await screen.findByRole("heading", { name: "Zone A" });
    expect(screen.queryByText("House climate")).not.toBeInTheDocument();
  });
});
