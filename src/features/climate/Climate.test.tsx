// T2-15 done-when: the Climate page shows fake data
import { screen } from "@testing-library/react";
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
});
