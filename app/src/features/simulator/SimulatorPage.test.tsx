import { screen, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { farmLayout } from "../../mocks/data/layout";
import { server } from "../../mocks/node";
import { renderAt } from "../../test/render";

const notPlayground = () =>
  server.use(
    http.get("/api/farm", () =>
      HttpResponse.json({ ...farmLayout, features: { ...farmLayout.features, playground: false } }),
    ),
  );

describe("Simulator screen", () => {
  it("opens with the three areas", async () => {
    renderAt("/simulator");
    expect(await screen.findByRole("heading", { level: 2, name: "Farm view" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1, name: "Simulator" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2, name: "Held values" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2, name: "Failure drills" })).toBeInTheDocument();
    expect(screen.getByText("No values are being held.")).toBeInTheDocument();
    expect(screen.getByText("No failure drills are running.")).toBeInTheDocument();
  });

  it("has no buttons for dose, refill or stop", async () => {
    renderAt("/simulator");
    await screen.findByRole("heading", { name: "Farm view" });
    expect(within(screen.getByRole("main")).queryAllByRole("button")).toEqual([]);
  });

  it("is linked in the menu on a playground site", async () => {
    renderAt("/");
    const main = within(screen.getByRole("navigation", { name: "Main" }));
    expect(await main.findByRole("link", { name: "Simulator" })).toHaveAttribute("href", "/simulator");
  });

  it("hides the link when features.playground is false", async () => {
    notPlayground();
    renderAt("/");
    const main = within(screen.getByRole("navigation", { name: "Main" }));
    await main.findByRole("link", { name: "Zone A" });
    expect(main.queryByRole("link", { name: "Simulator" })).not.toBeInTheDocument();
  });

  it("does not open from a typed URL when features.playground is false", async () => {
    notPlayground();
    renderAt("/simulator");
    expect(await screen.findByRole("heading", { name: "Simulator not available" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Farm view" })).not.toBeInTheDocument();
  });
});
