import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { server } from "../mocks/node";
import { renderAt } from "../test/render";

describe("App shell", () => {
  it("shows the top bar, navigation and the Overview page", async () => {
    renderAt("/");
    const banner = screen.getByRole("banner");
    expect(await within(banner).findByText("SIET Polyhouse 1")).toBeInTheDocument();
    expect(await within(banner).findByText("Connected")).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Main" })).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Tabs" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Overview" })).toBeInTheDocument();
  });

  it("says Server problem when the server answers 503 DEGRADED", async () => {
    server.use(
      http.get("/api/health", () =>
        HttpResponse.json({ status: "DEGRADED", database: "DOWN", broker: "UP" }, { status: 503 }),
      ),
    );
    renderAt("/");
    expect(await within(screen.getByRole("banner")).findByText("Server problem")).toBeInTheDocument();
  });

  it("says Not connected when the server can't be reached", async () => {
    server.use(http.get("/api/health", () => HttpResponse.error()));
    renderAt("/");
    expect(await within(screen.getByRole("banner")).findByText("Not connected")).toBeInTheDocument();
  });

  it("lists the zones from the farm in the sidebar", async () => {
    renderAt("/");
    const main = within(screen.getByRole("navigation", { name: "Main" }));
    expect(await main.findByRole("link", { name: "Zone B" })).toHaveAttribute("href", "/zone/Z2");
  });

  it("shows who is signed in at the foot of the sidebar", async () => {
    renderAt("/");
    const nav = within(screen.getByRole("navigation", { name: "Main" }));
    const card = within(await nav.findByRole("group", { name: "Signed in" }));
    expect(card.getByText("Owner")).toBeInTheDocument();
    expect(card.queryByText(/@/)).not.toBeInTheDocument(); // name and role only, as in the first demo
  });

  it.each([
    ["/zone/Z1", "Zone A"],
    ["/zone/Z2", "Zone B"],
    ["/alerts", "Alerts"],
    ["/history", "History"],
    ["/nowhere", "Page not found"],
  ])("routes %s to %s", async (path, heading) => {
    renderAt(path);
    expect(await screen.findByRole("heading", { name: heading })).toBeInTheDocument();
  });

  it("sends an old /climate link to the Overview: the climate is on each zone page now", async () => {
    renderAt("/climate");
    expect(await screen.findByRole("heading", { level: 1, name: "Overview" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Climate" })).not.toBeInTheDocument();
  });
});

describe("Side by side", () => {
  it("shows the Simulator beside the page on a playground site, and closes again", async () => {
    const user = userEvent.setup();
    renderAt("/zone/Z1");
    const banner = within(screen.getByRole("banner"));
    await user.click(await banner.findByRole("button", { name: "Side by side" }));
    expect(await screen.findByRole("heading", { level: 1, name: "Simulator" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1, name: /Zone A/ })).toBeInTheDocument(); // the page stays
    expect(screen.getByRole("separator", { name: "Resize panes" })).toBeInTheDocument();
    await user.click(banner.getByRole("button", { name: "Close side by side" }));
    expect(screen.queryByRole("heading", { level: 1, name: "Simulator" })).not.toBeInTheDocument();
    localStorage.clear();
  });

  it("has no Side by side on a real farm, which has no simulator", async () => {
    window.history.replaceState(null, "", "/?mock=no-playground");
    renderAt("/");
    await screen.findByRole("heading", { level: 1, name: "Overview" });
    expect(screen.queryByRole("button", { name: "Side by side" })).not.toBeInTheDocument();
    window.history.replaceState(null, "", "/");
    window.sessionStorage.clear();
  });
});
