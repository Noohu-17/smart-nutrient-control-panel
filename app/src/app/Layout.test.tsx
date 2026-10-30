import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { renderAt } from "../test/render";

describe("App shell", () => {
  it("shows the top bar, navigation and the Overview page", async () => {
    renderAt("/");
    const banner = screen.getByRole("banner");
    expect(await within(banner).findByText("SIET Polyhouse 1")).toBeInTheDocument();
    expect(banner).toHaveTextContent("Not connected");
    expect(screen.getByRole("navigation", { name: "Main" })).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Tabs" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Overview" })).toBeInTheDocument();
  });

  it("lists the zones from the farm in the sidebar", async () => {
    renderAt("/");
    const main = within(screen.getByRole("navigation", { name: "Main" }));
    expect(await main.findByRole("link", { name: "Zone B" })).toHaveAttribute("href", "/zone/Z2");
  });

  it.each([
    ["/zone/Z1", "Zone A"],
    ["/zone/Z2", "Zone B"],
    ["/climate", "Climate"],
    ["/alerts", "Alerts"],
    ["/history", "History"],
    ["/nowhere", "Page not found"],
  ])("routes %s to %s", async (path, heading) => {
    renderAt(path);
    expect(await screen.findByRole("heading", { name: heading })).toBeInTheDocument();
  });
});
