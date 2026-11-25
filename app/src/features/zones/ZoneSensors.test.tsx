// DM-3 · a card per sensor of the zone's farm type: fitted ones live, the rest greyed "Not connected"
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

const sensorsSection = async () =>
  within((await screen.findByRole("heading", { name: "Sensors" })).closest("section")!);

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

describe("Sensors on the zone page", () => {
  it("shows a card for each of the ten hydroponics sensors from the sheet", async () => {
    renderAt("/zone/Z1");
    const section = await sensorsSection();
    expect(section.getAllByRole("listitem").map((li) => li.getAttribute("aria-label"))).toEqual(TEN_SENSORS);
    expect(
      section.getByText("Hydroponics (NFT, DWC, vertical): roots in nutrient water"),
    ).toBeInTheDocument();
  });

  it("client_A: what the layout fits shows live, the rest Not connected", async () => {
    renderAt("/zone/Z1");
    const section = await sensorsSection();
    expect(section.getByText("7 of 10 connected")).toBeInTheDocument();
    const card = (name: string) => within(section.getByRole("listitem", { name }));
    expect(await valueOf(section.getByRole("listitem", { name: "pH sensor" }))).toMatch(/^\d+\.\d{2}$/);
    expect(await valueOf(section.getByRole("listitem", { name: "Leak / overflow sensor" }))).toBe("Dry");
    for (const name of ["Dissolved oxygen (DO) sensor", "CO₂ sensor", "Light (PAR) sensor"]) {
      expect(card(name).getByText("Not connected")).toBeInTheDocument();
      expect(card(name).getByText(/^Would measure: /)).toBeInTheDocument();
    }
    expect(card("CO₂ sensor").getByText("Typical 400 – 1,000 ppm · Good to have")).toBeInTheDocument();
    expect(card("pH sensor").getByText("Typical 5.5 – 6.5 · Must have")).toBeInTheDocument();
  });

  it("is left out for a pack without a sensor catalogue", async () => {
    window.history.replaceState(null, "", "/zone/Z1?mock=no-sensors");
    renderAt("/zone/Z1");
    await screen.findByRole("heading", { name: "Benches" });
    await new Promise((r) => setTimeout(r, 400)); // the 404 has come
    expect(screen.queryByRole("heading", { name: "Sensors" })).not.toBeInTheDocument();
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
  const LIVE = ["pH sensor", "EC / TDS sensor", "Water temperature sensor", "Water level sensor"];
  const NOT_CONNECTED = [
    "Flow sensor",
    "Dissolved oxygen (DO) sensor",
    "CO₂ sensor",
    "Light (PAR) sensor",
    "Leak / overflow sensor",
  ];

  it.each((layout.zones as FarmZone[]).map((z) => [z.id, z.name]))(
    "%s (%s) shows all ten: four live, the air simulated, the rest Not connected",
    async (zoneId) => {
      useClientB();
      renderAt(`/zone/${zoneId}`);
      const section = await sensorsSection();
      expect(section.getAllByRole("listitem")).toHaveLength(10);
      expect(section.getByText("5 of 10 connected")).toBeInTheDocument();
      for (const name of LIVE) {
        const card = section.getByRole("listitem", { name });
        expect(within(card).getByText("Live")).toBeInTheDocument();
        expect(await valueOf(card)).toMatch(/^\d/);
      }
      const air = section.getByRole("listitem", { name: "Air temperature & humidity sensor" });
      expect(within(air).getByText("Simulated")).toBeInTheDocument();
      expect(within(air).getByText("Air temp")).toBeInTheDocument();
      expect(within(air).getByText("Humidity")).toBeInTheDocument();
      expect(await valueOf(air)).toMatch(/^\d+\.\d$/);
      for (const name of NOT_CONNECTED) {
        const card = section.getByRole("listitem", { name });
        expect(card).toHaveClass("not-connected");
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
