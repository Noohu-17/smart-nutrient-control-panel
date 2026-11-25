// src/config/zoneText.test.ts · T2-30 · how client_B's zones, crops and nutrients read
import { describe, expect, it } from "vitest";
import layoutB from "../../../config/clients/client_B/layout.json";
import nutrientsB from "../../../config/clients/client_B/nutrients.json";
import type { DoseLineLimit, FarmZone, ZoneCrop } from "../api/types";
import {
  cropDay,
  cropDayText,
  doseLineLabel,
  nutrientDetails,
  nutrientText,
  plantedText,
  unitDetail,
  zoneProfile,
  zoneSource,
  zoneTags,
} from "./zoneText";

const IST = "Asia/Kolkata";
const NOW = new Date("2026-11-18T06:00:00Z"); // 11:30 on 18 Nov at the farm
const zones = layoutB.zones as FarmZone[];
const lettuce: ZoneCrop = {
  crop: "LETTUCE",
  name: "Lettuce",
  variety: "Grand Rapids",
  stage: "SEEDLING",
  plantedOn: "2026-11-10",
};

const lineInfo = (line: "A" | "B" | "C"): DoseLineLimit => {
  const { name, compound, strengthPct, drum, purpose } = nutrientsB.doseLines[line];
  return {
    line,
    name,
    compound,
    strengthPct,
    drum,
    purpose,
    pumpMlPerMin: 15,
    maxPerDoseMl: 2,
    max24hMl: 8,
    dosed24hMl: 0,
    left24hMl: 8,
  };
};

describe("zone tags", () => {
  it("read client_B's Zone A as the farm describes it", () => {
    const profile = { ...zoneProfile(zones[0]), link: "SF_GATEWAY" };
    expect(zoneTags(profile, lettuce, { timezone: IST, source: "LIVE", now: NOW })).toEqual([
      "NFT",
      "4 benches",
      "Lettuce",
      "Seedling",
      "day 9",
      "90 L tank",
    ]);
  });

  it("say NOT FITTED for a zone with no sensors fitted yet", () => {
    const bare = {
      ...zones[1],
      sensors: Object.fromEntries(Object.keys(zones[1].sensors!).map((s) => [s, false])),
    };
    const tags = zoneTags(zoneProfile(bare), null, { timezone: IST, source: "LIVE", now: NOW });
    expect(tags).toEqual(["NFT", "4 benches", "90 L tank", "NOT FITTED"]);
  });

  it("leave Live / Simulated to T2-26's chip, and show the harvest day once the pack has it", () => {
    const crop = { ...lettuce, daysToHarvest: 35 };
    const tags = zoneTags(zoneProfile(zones[0]), crop, { timezone: IST, source: "SIMULATED", now: NOW });
    expect(tags).toContain("day 9 of 35");
    expect(tags).not.toContain("SIMULATED");
    expect(tags).not.toContain("LIVE");
  });

  it("don't say NOT FITTED for a simulated zone", () => {
    const bare = {
      ...zones[1],
      sensors: Object.fromEntries(Object.keys(zones[1].sensors!).map((s) => [s, false])),
    };
    const tags = zoneTags(zoneProfile(bare), null, { timezone: IST, source: "SIMULATED", now: NOW });
    expect(tags).toEqual(["NFT", "4 benches", "90 L tank"]);
  });

  it("take the zone's source from its answer, then the layout, then the site", () => {
    const zone = zones[0]; // LIVE in client_B's layout
    expect(zoneSource(zone, "SIMULATED", false)).toBe("SIMULATED");
    expect(zoneSource(zone, undefined, true)).toBe("LIVE"); // a live zone on a playground site
    const unsaid = { ...zone, source: undefined } as unknown as FarmZone; // an older back end
    expect(zoneSource(unsaid, undefined, true)).toBe("SIMULATED");
    expect(zoneSource(unsaid, undefined, false)).toBe("LIVE");
  });

  it("leave out what a pack from before T1-38 doesn't say", () => {
    const old: FarmZone = {
      ...zones[0],
      growingSystem: undefined,
      growingUnits: undefined,
      sensors: undefined,
    };
    expect(zoneTags(zoneProfile(old), undefined, { timezone: IST, source: "LIVE", now: NOW })).toEqual([
      "90 L tank",
    ]);
  });
});

describe("crop day", () => {
  it("counts the planting day as day 1, on the farm's own calendar", () => {
    expect(cropDay("2026-11-18", IST, NOW)).toBe(1);
    // 20:00 UTC on 17 Nov is already 18 Nov in India
    expect(cropDay("2026-11-10", IST, new Date("2026-11-17T20:00:00Z"))).toBe(9);
    expect(cropDay("2026-11-10", "UTC", new Date("2026-11-17T20:00:00Z"))).toBe(8);
  });

  it("is unknown with no planting day, or one still to come", () => {
    expect(cropDay(null, IST, NOW)).toBeNull();
    expect(cropDay("2026-12-01", IST, NOW)).toBeNull();
    expect(cropDayText({ ...lettuce, plantedOn: null }, IST, NOW)).toBeNull();
  });

  it("writes the planting day as a person would", () => {
    expect(plantedText("2026-11-10")).toBe("10 Nov 2026");
  });
});

describe("growing units and nutrients", () => {
  it("describe an NFT structure with its channels, length and plant sites", () => {
    expect(unitDetail(zones[0].growingUnits![0])).toBe(
      "4 benches · 3 channels × 2.1 m · 45 plant sites each",
    );
    expect(zoneProfile(zones[0]).plantSites).toBe(180);
  });

  it("label each of client_B's amounts as its drum, with what the stock is underneath", () => {
    expect(doseLineLabel("A", lineInfo("A"))).toBe("Stock A");
    expect(nutrientText(lineInfo("A"))).toBe("Grow: nitrogen and calcium");
    expect(nutrientDetails(lineInfo("A"))).toBe(
      "City Greens Grow 24-00-00, N 24% · Vegetative growth: leaves, chlorophyll and strong cell walls",
    );
    expect(nutrientText(lineInfo("B"))).toBe("Flora: phosphorus");
    expect(nutrientText(lineInfo("C"))).toBe("Thrive: potassium and magnesium");
  });

  it("name the nutrient in the label of a line with no name of its own", () => {
    const n = { ...lineInfo("A"), line: "N", name: "Nitrogen" };
    expect(doseLineLabel("N", n)).toBe("Nitrogen (N)");
    expect(nutrientText(n)).toBe("City Greens Grow 24-00-00, N 24%");
  });

  it("show the product when the name only repeats the line's label", () => {
    const stockA = { ...lineInfo("A"), name: "Stock A", compound: "Calcium nitrate", strengthPct: 10 };
    expect(nutrientText({ ...stockA, purpose: "Nitrogen and calcium" })).toBe("Calcium nitrate 10%");
    expect(nutrientDetails({ ...stockA, purpose: "Nitrogen and calcium" })).toBe("Nitrogen and calcium");
  });

  it("say nothing for a line the pack doesn't describe", () => {
    const bare = { ...lineInfo("A"), name: null, compound: null, strengthPct: null, purpose: null };
    expect(nutrientText(bare)).toBeNull();
    expect(nutrientText(undefined)).toBeNull();
  });
});
