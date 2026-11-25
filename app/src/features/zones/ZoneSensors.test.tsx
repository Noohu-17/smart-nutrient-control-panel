// DM-3 · the zone's readings: every sensor of its farm type, fitted ones live, the rest greyed "Not connected"
import { screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { afterEach, describe, expect, it } from "vitest";
import features from "../../../../config/clients/client_B/features.json";
import layout from "../../../../config/clients/client_B/layout.json";
import clientBSensors from "../../../../config/clients/client_B/sensors.json";
import type { Farm, FarmZone, SensorInfo, SensorReading } from "../../api/types";
import { sensorCatalogue } from "../../mocks/data/sensors";
import { server } from "../../mocks/node";
import { renderAt } from "../../test/render";
import { climateSource, formatSensorValue, isFitted, readingValue, zoneFarmType } from "./sensorText";

const TEN_SENSORS = [
  "pH sensor",
  "EC / TDS sensor",
  "Water temperature sensor",
  "Water level sensor",
  "Flow sensor",
  "Air temperature & humidity sensor",
  "Dissolved oxygen (DO) sensor",
  "CO₂ sensor",
  "Light (PAR) sensor",
  "Leak / overflow sensor",
];

const readingsSection = async () =>
  within((await screen.findByRole("heading", { name: "Readings" })).closest("section")!);

/** The card's value once its answer has come: a number, or a yes/no word such as Dry. */
const valueOf = async (card: HTMLElement) => {
  await waitFor(() => expect(card.querySelector(".reading-value b")?.textContent).not.toBe("--"));
  return card.querySelector(".reading-value b")!.textContent;
};

afterEach(() => {
  window.history.replaceState(null, "", "/");
  window.sessionStorage.clear();
});

describe("sensorText", () => {
  const hydroponics = sensorCatalogue.farmTypes.HYDROPONICS.sensors;
  const byId = (id: string) => hydroponics.find((s) => s.id === id)!;
  const zone = { growingSystem: "NFT", sensors: { ph: true, circFlow: false } } as unknown as FarmZone;
  const farm = { climate: { controller: "CC1" }, features: { playground: false } } as unknown as Farm;

  it("finds the zone's farm type from its growing system", () => {
    expect(zoneFarmType(sensorCatalogue, zone)?.sensors).toHaveLength(10);
    expect(zoneFarmType(sensorCatalogue, { ...zone, growingSystem: "DRIP" })).toBeNull();
    expect(zoneFarmType(sensorCatalogue, { ...zone, growingSystem: undefined })).toBeNull();
  });

  it("is fitted by the layout's zone sensors, or for the air by the climate controller", () => {
    expect(isFitted(byId("ph"), zone, farm)).toBe(true);
    expect(isFitted(byId("flow"), zone, farm)).toBe(false);
    expect(isFitted(byId("leak"), zone, farm)).toBe(false); // left out of the zone's sensors
    expect(isFitted(byId("co2"), zone, farm)).toBe(false); // no layout sensor for it yet
    expect(isFitted(byId("airTempRh"), zone, farm)).toBe(true);
    expect(isFitted(byId("airTempRh"), zone, { ...farm, climate: { controller: "" } } as Farm)).toBe(false);
  });

  it("takes the climate's source from the layout, or as for a zone when it says none", () => {
    expect(climateSource({ ...farm, climate: { controller: "CC1", source: "SIMULATED" } } as Farm)).toBe(
      "SIMULATED",
    );
    expect(climateSource(farm)).toBe("LIVE");
    expect(climateSource({ ...farm, features: { playground: true } } as Farm)).toBe("SIMULATED");
  });

  it("reads and words each value", () => {
    const ph: SensorReading = byId("ph").readings[0];
    const leak: SensorReading = byId("leak").readings[0];
    const zoneAnswer = { readings: { ph: 6.054 }, leak: false } as never;
    expect(formatSensorValue(ph, readingValue(ph, zoneAnswer))).toBe("6.05");
    expect(formatSensorValue(leak, readingValue(leak, zoneAnswer))).toBe("Dry");
    expect(formatSensorValue(leak, true)).toBe("Wet");
    expect(formatSensorValue(ph, readingValue(ph, { readings: { ph: null } } as never))).toBe("--");
    expect(readingValue(ph)).toBeUndefined(); // the answer hasn't come yet
  });
});

// client_A's Zone A has every layout sensor but dissolved oxygen; CO₂ and light have none yet
const MISSING_ON_A = ["Dissolved oxygen (DO) sensor", "CO₂ sensor", "Light (PAR) sensor"];

describe("Readings on the zone page", () => {
  it("client_A: what the layout fits shows live, the rest greyed Not connected, last", async () => {
    renderAt("/zone/Z1");
    const section = await readingsSection();
    expect(await section.findByText("7 of 10 sensors connected")).toBeInTheDocument();
    expect(await valueOf(section.getByRole("group", { name: "pH" }))).toMatch(/^\d+\.\d{2}$/);
    expect(section.getByRole("group", { name: "Solution temp" })).toBeInTheDocument(); // the app's own label
    expect(await valueOf(section.getByRole("group", { name: "Leak" }))).toBe("Dry");
    const groups = section.getAllByRole("group").map((g) => g.getAttribute("aria-label"));
    expect(groups.slice(-3)).toEqual(MISSING_ON_A); // the missing ones last
    for (const name of MISSING_ON_A) {
      const card = section.getByRole("group", { name });
      expect(card).toHaveClass("not-connected");
      expect(within(card).getByText("Not connected")).toBeInTheDocument();
      expect(within(card).getByText(/^Would measure /)).toBeInTheDocument();
    }
  });

  it("has no separate Sensors section", async () => {
    renderAt("/zone/Z1");
    await readingsSection();
    await screen.findByRole("heading", { name: "Benches" });
    expect(screen.queryByRole("heading", { name: "Sensors" })).not.toBeInTheDocument();
  });

  it("shows the zone's main readings as before for a pack without a sensor catalogue", async () => {
    window.history.replaceState(null, "", "/zone/Z1?mock=no-sensors");
    renderAt("/zone/Z1");
    const section = await readingsSection();
    for (const name of ["EC", "pH", "Solution temp", "Reservoir level"])
      expect(await section.findByRole("group", { name })).toBeInTheDocument();
    await new Promise((r) => setTimeout(r, 400)); // the 404 has come
    expect(section.queryByText(/sensors connected$/)).not.toBeInTheDocument();
  });
});

// DM-3 done when: client_B zone pages show all ten sensors: four live, air readings simulated, the rest
// greyed "Not connected". GET /api/farm and /api/sensors answer client_B's own config pack.
describe("client_B zone pages", () => {
  const catalogue = {
    v: clientBSensors.v,
    growingSystems: clientBSensors.growingSystems,
    farmTypes: clientBSensors.farmTypes,
  };
  const useClientB = () =>
    server.use(
      http.get("/api/farm", () => HttpResponse.json({ ...layout, features })),
      http.get("/api/sensors", () => HttpResponse.json(catalogue)),
    );
  const NOT_CONNECTED = [
    "Flow sensor",
    "Dissolved oxygen (DO) sensor",
    "CO₂ sensor",
    "Light (PAR) sensor",
    "Leak / overflow sensor",
  ];

  it.each((layout.zones as FarmZone[]).map((z) => [z.id, z.name]))(
    "%s (%s) shows all ten in Readings: four live, the air simulated, the rest Not connected",
    async (zoneId) => {
      useClientB();
      renderAt(`/zone/${zoneId}`);
      const section = await readingsSection();
      expect(await section.findByText("5 of 10 sensors connected")).toBeInTheDocument();
      for (const name of ["EC", "pH", "Solution temp", "Reservoir level"])
        expect(await valueOf(section.getByRole("group", { name }))).toMatch(/^\d/);
      for (const name of ["Air temp", "Humidity"]) {
        const card = section.getByRole("group", { name });
        expect(within(card).getByText("Simulated")).toBeInTheDocument();
        expect(await valueOf(card)).toMatch(/^\d/);
      }
      const groups = section.getAllByRole("group").map((g) => g.getAttribute("aria-label"));
      expect(groups.slice(-5)).toEqual(NOT_CONNECTED); // the missing ones last
      for (const name of NOT_CONNECTED) {
        const card = section.getByRole("group", { name });
        expect(card).toHaveClass("not-connected");
        expect(card.parentElement).toHaveClass("readings-missing"); // their own rows, under the live ones
        expect(within(card).getByText("Not connected")).toBeInTheDocument();
        expect(card.querySelector(".reading-value")).toBeNull();
      }
    },
  );

  it("takes client_B's catalogue as it is in the pack", () => {
    const sensors = catalogue.farmTypes.HYDROPONICS.sensors as SensorInfo[];
    expect(sensors.map((s) => s.name)).toEqual(TEN_SENSORS);
    expect(layout.climate.source).toBe("SIMULATED");
  });
});
