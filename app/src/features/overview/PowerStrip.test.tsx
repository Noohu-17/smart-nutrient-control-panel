// T2-15 done-when: the power strip shows fake data, with no generator item when the layout has none
import { screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { renderAt } from "../../test/render";

const strip = () => screen.findByRole("region", { name: "Power" });

afterEach(() => {
  window.history.replaceState(null, "", "/");
  window.sessionStorage.clear();
});

describe("Overview power strip", () => {
  it("shows mains, generator and UPS from the fake POWER_STATE", async () => {
    renderAt("/");
    const power = await strip();
    expect(await within(power).findByText("On")).toBeInTheDocument();
    expect(within(power).getByRole("group", { name: "Mains" })).toHaveTextContent("231 / 229 / 233 V");
    expect(within(power).getByRole("group", { name: "Generator" })).toHaveTextContent("Standby");
    expect(within(power).getByRole("group", { name: "UPS" })).toHaveTextContent("100% · 42 min");
  });

  it("says when mains is lost and the generator is running", async () => {
    window.history.replaceState(null, "", "/?mock=mains-lost");
    renderAt("/");
    const power = await strip();
    expect(await within(power).findByText("Lost")).toBeInTheDocument();
    expect(within(power).getByRole("group", { name: "Mains" })).toHaveTextContent("-- / -- / -- V");
    expect(within(power).getByRole("group", { name: "Generator" })).toHaveTextContent("Running");
    expect(within(power).getByRole("group", { name: "UPS" })).toHaveTextContent("On battery");
  });

  it("has no generator or UPS item when the layout has none", async () => {
    window.history.replaceState(null, "", "/?mock=no-generator");
    renderAt("/");
    const power = await strip();
    expect(await within(power).findByText("On")).toBeInTheDocument();
    expect(within(power).queryByRole("group", { name: "Generator" })).not.toBeInTheDocument();
    expect(within(power).queryByRole("group", { name: "UPS" })).not.toBeInTheDocument();
  });
});
