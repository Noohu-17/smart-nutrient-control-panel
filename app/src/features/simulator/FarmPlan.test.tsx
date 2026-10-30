import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { Farm } from "../../api/types";
import { farmLayout } from "../../mocks/data/layout";
import { renderAt } from "../../test/render";
import { FarmPlan } from "./FarmPlan";

function drawn(farm: Farm) {
  const { container } = render(<FarmPlan farm={farm} />);
  const count = (selector: string) => container.querySelectorAll(selector).length;
  return {
    plan: within(screen.getByRole("img", { name: `${farm.name} plan` })),
    benches: count(".plan-bench"),
    channels: count(".plan-channel"),
    fans: count(".plan-fan"),
    pads: count(".plan-pad"),
  };
}

describe("Farm plan", () => {
  it("draws every zone, bench, channel, fan and the pads for 2 zones", () => {
    const { plan, benches, channels, fans, pads } = drawn(farmLayout);
    expect(plan.getByText("Zone A")).toBeInTheDocument();
    expect(plan.getByText("Zone B")).toBeInTheDocument();
    expect(plan.getByText("Z2-B4")).toBeInTheDocument();
    expect({ benches, channels, fans, pads }).toEqual({ benches: 8, channels: 48, fans: 4, pads: 1 });
  });

  it("works for 1 zone", () => {
    const { plan, benches, channels } = drawn({ ...farmLayout, zones: [farmLayout.zones[0]] });
    expect(plan.getByText("Zone A")).toBeInTheDocument();
    expect(plan.queryByText("Zone B")).not.toBeInTheDocument();
    expect({ benches, channels }).toEqual({ benches: 4, channels: 24 });
  });

  it("follows the layout's own counts, with nothing fixed in code", () => {
    const zone = { ...farmLayout.zones[0], benches: [{ id: "X-B1", channels: 3, lengthM: 5 }] };
    const climate = { ...farmLayout.climate, fans: ["F1", "F2"], padPumps: [] };
    const { plan, benches, channels, fans, pads } = drawn({ ...farmLayout, zones: [zone], climate });
    expect(plan.getByText("X-B1")).toBeInTheDocument();
    expect({ benches, channels, fans, pads }).toEqual({ benches: 1, channels: 3, fans: 2, pads: 0 });
  });

  it("is shown in the Farm view area of the Simulator screen", async () => {
    renderAt("/simulator");
    const area = (await screen.findByRole("heading", { name: "Farm view" })).closest("section")!;
    expect(within(area).getByRole("img", { name: "SIET Polyhouse 1 plan" })).toBeInTheDocument();
  });
});
