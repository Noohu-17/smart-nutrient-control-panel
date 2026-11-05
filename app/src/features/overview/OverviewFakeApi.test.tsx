// T2-4 done-when: the Overview shows Zone A and Zone B from fake data
import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { renderAt } from "../../test/render";

// the fake API waits 300 ms per call, so allow more than the 1 s default
const WAIT = { timeout: 3000 };

describe("Overview on the fake API", () => {
  it("shows both zone names that came from /api/farm", async () => {
    renderAt("/");
    expect(await screen.findByRole("heading", { name: "Zone A" }, WAIT)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Zone B" })).toBeInTheDocument();
  });
});
