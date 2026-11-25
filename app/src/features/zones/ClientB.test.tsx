// src/features/zones/ClientB.test.tsx · T2-30 · client_B on screen, with the fake API's zone and
// dosing answers replaced by the real back end's for client_B: each one built from
// config/clients/client_B the way backend/app/api (pack.py, T1-38) builds it.
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import features from "../../../../config/clients/client_B/features.json";
import hardware from "../../../../config/clients/client_B/hardware.json";
import ioMap from "../../../../config/clients/client_B/io_map.json";
import layout from "../../../../config/clients/client_B/layout.json";
import limits from "../../../../config/clients/client_B/limits.json";
import nutrients from "../../../../config/clients/client_B/nutrients.json";
import recipes from "../../../../config/clients/client_B/recipes.json";
import type { DoseLimits, DoseRequest, FarmZone, ZoneStatus } from "../../api/types";
import { zoneProfile } from "../../config/zoneText";
import { server } from "../../mocks/node";
import { renderAt } from "../../test/render";

const zones = layout.zones as FarmZone[];
const zoneById = (id: string) => zones.find((z) => z.id === id)!;
type Applied = { crop: string; variety: string; stage: string; plantedOn: string };
type Line = "A" | "B" | "C";

/** GET /api/zones/{zoneId}: a zone that hasn't reported yet (§11.2), with its profile and crop. */
function zoneAnswer(zone: FarmZone): ZoneStatus {
  const links = ioMap.controllers as Record<string, { link?: string }>;
  const applied = (recipes.zones as Record<string, Applied>)[zone.id];
  const lines = Object.fromEntries(zone.doseLines.map((line) => [line, null]));
  return {
    zone: zone.id,
    source: zone.source,
    online: false,
    lastSeen: null,
    ts: null,
    readings: {
      ec: null,
      ph: null,
      solTempC: null,
      reservoirPct: null,
      reservoirL: null,
      circFlowLpm: null,
      doMgL: null,
      dayTankPct: lines,
      dayTankL: lines,
    },
    quality: {},
    equipment: {},
    mode: null,
    estop: null,
    leak: null,
    stopped: null,
    activeCmdId: null,
    operation: null,
    latches: [],
    configVersion: null,
    profile: { ...zoneProfile(zone), link: links[zone.controller]?.link ?? "MQTT" },
    crop: applied && {
      ...applied,
      name: recipes.crops.find((c) => c.id === applied.crop)!.name,
    },
  };
}

type Pumps = Partial<Record<Line, { flowMlPerS: number }>>;

/** hardware.json's pump rate of a line in a zone: the zone's own (pumps.zones), else the shared one (T1-31). */
function pumpOf(zoneId: string, line: Line) {
  const own = (hardware.pumps.zones as Record<string, { doseLines: Pumps } | undefined>)[zoneId];
  return own?.doseLines[line] ?? hardware.pumps.doseLines[line];
}

/** GET /api/zones/{zoneId}/dose-limits: limits.json, nutrients.json and hardware.json. */
function doseLimitsAnswer(zone: FarmZone): DoseLimits {
  const zoneLimits = limits.zones[zone.id as "Z1"].limits;
  return {
    zone: zone.id,
    lines: (zone.doseLines as Line[]).map((line) => {
      const { name, compound, strengthPct, drum, purpose } = nutrients.doseLines[line];
      return {
        line,
        name,
        compound,
        strengthPct,
        drum,
        purpose,
        pumpMlPerMin: pumpOf(zone.id, line).flowMlPerS * 60,
        maxPerDoseMl: zoneLimits.maxPerDoseMl[line],
        max24hMl: zoneLimits.max24hMl[line],
        dosed24hMl: 0,
        left24hMl: zoneLimits.max24hMl[line],
      };
    }),
  };
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-11-18T06:00:00Z")); // 18 Nov at the farm: day 9 of a crop planted on the 10th
  server.use(
    http.get("/api/farm", () => HttpResponse.json({ ...layout, features })),
    http.get("/api/zones/:zoneId/dose-limits", ({ params }) =>
      HttpResponse.json(doseLimitsAnswer(zoneById(String(params.zoneId)))),
    ),
    http.get("/api/zones/:zoneId", ({ params }) =>
      HttpResponse.json(zoneAnswer(zoneById(String(params.zoneId)))),
    ),
    http.post("/api/dose-requests", async ({ request }) => {
      const body = (await request.json()) as { zone: string; lines: { line: string; ml: number }[] };
      const answer: DoseRequest = {
        id: "dr_1",
        zone: body.zone,
        lines: body.lines,
        projected: { ec: null, ph: null },
        checks: [{ id: "DEVICES_ONLINE", result: "BLOCK", message: "ZC1 has never reported" }],
        estimate: { durationMin: 11, totalMl: 2 },
        canConfirm: false,
      };
      return HttpResponse.json(answer, { status: 201 });
    }),
  );
});

afterEach(() => vi.useRealTimers());

const tagsOf = async (zoneName: string) =>
  within(await screen.findByRole("list", { name: `About ${zoneName}` }))
    .getAllByRole("listitem")
    .map((li) => li.textContent);

describe("client_B with the real back end's answers", () => {
  it("tags each zone card with what it is and grows", async () => {
    renderAt("/");
    await screen.findByText("Lettuce");
    expect(await tagsOf("NFT Zone A")).toEqual([
      "NFT",
      "4 benches",
      "Lettuce",
      "Seedling",
      "day 9",
      "90 L tank",
    ]);
    expect(await tagsOf("NFT Zone B")).toEqual([
      "NFT",
      "4 benches",
      "Spinach",
      "Seedling",
      "day 9",
      "90 L tank",
    ]);
    expect(screen.queryByRole("list", { name: "About NFT Zone C" })).not.toBeInTheDocument(); // two zones
  });

  it("shows Zone A's details on its page", async () => {
    renderAt("/zone/Z1");
    const panel = within((await screen.findByRole("heading", { name: "Zone details" })).closest("section")!);
    const row = (term: string) => panel.getByText(term, { selector: "dt" }).nextElementSibling?.textContent;
    await panel.findByText("Lettuce · Grand Rapids");
    expect(row("Stage")).toBe("Seedling");
    expect(row("Planted")).toBe("10 Nov 2026 · day 9");
    expect(row("Growing system")).toBe("NFT");
    expect(row("Growing units")).toBe("4 benches · 3 channels × 2.1 m · 45 plant sites each");
    expect(row("Plant sites")).toBe("180");
    expect(row("Zone size")).toBe("3 × 4 m");
    expect(row("Position")).toBe("First NFT bay from the door");
    expect(row("Tank")).toBe("R1 · 90 L");
    expect(row("Sensors fitted")).toBe(
      "EC, pH, Solution temp, Tank levelNot fitted: Circulation flow, Day-tank level, Leak, Dissolved oxygen",
    );
    expect(row("Controller")).toBe("ZC1 · Prototype rig gateway");
    expect(row("Notes")).toMatch(/^Runs on the prototype rig's Zone 1/);
  });

  it("shows Zone B on the rig's Zone 2 (T1-31)", async () => {
    renderAt("/zone/Z2");
    const panel = within((await screen.findByRole("heading", { name: "Zone details" })).closest("section")!);
    const row = (term: string) => panel.getByText(term, { selector: "dt" }).nextElementSibling?.textContent;
    await panel.findByText("Spinach · Ooty 1");
    expect(row("Controller")).toBe("ZC2 · Prototype rig gateway");
    expect(row("Notes")).toMatch(/^Runs on the prototype rig's Zone 2/);
  });

  it("describes each nutrient under its amount and in the review", async () => {
    const user = userEvent.setup();
    renderAt("/zone/Z1", { role: "Operator" });
    const card = within((await screen.findByRole("heading", { name: "Manual dose" })).closest("section")!);
    const grow =
      "Grow: nitrogen and calcium · City Greens Grow 24-00-00, N 24% · Vegetative growth: leaves, chlorophyll and strong cell walls";
    await card.findByText(grow);
    // each amount is described by its limits (T1-37), then what it doses
    const limit = "Max 2 mL per dose · 8 mL left today";
    expect(card.getByLabelText("Stock A")).toHaveAccessibleDescription(`${limit} ${grow}`);
    expect(card.getByLabelText("Stock B")).toHaveAccessibleDescription(
      `${limit} Flora: phosphorus · City Greens Flora 05-26-00, P 26% · Root development, flowering and overall growth`,
    );
    expect(card.getByLabelText("Stock C")).toHaveAccessibleDescription(
      `${limit} Thrive: potassium and magnesium · City Greens Thrive 06-00-19, K 19% · Overall growth and plant strength`,
    );

    await user.type(card.getByLabelText("Stock A"), "2");
    await user.click(card.getByRole("button", { name: "Review dose" }));
    await screen.findByRole("heading", { name: "Review safety checks" });
    const summary = screen.getByText("2 mL", { selector: ".dose-summary b" }).closest("li")!;
    expect(summary).toHaveTextContent(grow);
  });
});
