import { render, screen } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { routes } from "./routes";

function renderAt(path: string) {
  render(<RouterProvider router={createMemoryRouter(routes, { initialEntries: [path] })} />);
}

describe("App shell", () => {
  it("shows the top bar, navigation and the Overview page", () => {
    renderAt("/");
    expect(screen.getByRole("banner")).toHaveTextContent("SIET Polyhouse 1");
    expect(screen.getByRole("banner")).toHaveTextContent("Not connected");
    expect(screen.getByRole("navigation", { name: "Main" })).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Tabs" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Overview" })).toBeInTheDocument();
  });

  it.each([
    ["/zone/Z1", "Zone A"],
    ["/zone/Z2", "Zone B"],
    ["/climate", "Climate"],
    ["/alerts", "Alerts"],
    ["/history", "History"],
    ["/nowhere", "Page not found"],
  ])("routes %s to %s", (path, heading) => {
    renderAt(path);
    expect(screen.getByRole("heading", { name: heading })).toBeInTheDocument();
  });
});
