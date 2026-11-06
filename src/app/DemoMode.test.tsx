// T2-17 · VITE_DEMO_MODE: on, the role picker and the "Demo · simulated farm" banner; off, neither
import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SessionProvider } from "../auth/SessionProvider";
import { isDemoMode } from "../config/demo";
import { renderAt } from "../test/render";
import { makeQueryClient } from "./queryClient";
import { routes } from "./routes";

const BANNER = /Demo · simulated farm/;

afterEach(() => {
  vi.unstubAllEnvs();
  localStorage.clear();
});

/** The real app with the session read from browser storage, as main.tsx starts it. */
function renderFromStorage(path: string) {
  return render(
    <SessionProvider>
      <QueryClientProvider client={makeQueryClient({ retry: false })}>
        <RouterProvider router={createMemoryRouter(routes, { initialEntries: [path] })} />
      </QueryClientProvider>
    </SessionProvider>,
  );
}

describe("isDemoMode", () => {
  it("is on only with VITE_DEMO_MODE=true in a build, and on in development unless set to false", () => {
    vi.stubEnv("DEV", false);
    vi.stubEnv("VITE_DEMO_MODE", undefined);
    expect(isDemoMode()).toBe(false); // a build without the flag
    vi.stubEnv("VITE_DEMO_MODE", "true");
    expect(isDemoMode()).toBe(true);
    vi.stubEnv("DEV", true);
    vi.stubEnv("VITE_DEMO_MODE", "false");
    expect(isDemoMode()).toBe(false);
    vi.stubEnv("VITE_DEMO_MODE", undefined);
    expect(isDemoMode()).toBe(true);
  });
});

describe("demo mode on", () => {
  it("shows the role picker and the banner on the sign-in page", async () => {
    vi.stubEnv("VITE_DEMO_MODE", "true");
    renderAt("/sign-in", { role: null });
    expect(await screen.findByRole("heading", { name: "Sign in as" })).toBeInTheDocument();
    for (const role of ["Owner", "Agronomist", "Operator", "Viewer"]) {
      expect(screen.getByRole("button", { name: new RegExp(`^${role}`) })).toBeInTheDocument();
    }
    expect(screen.getByRole("note")).toHaveTextContent(BANNER);
  });

  it("keeps the banner on every screen after sign-in", async () => {
    vi.stubEnv("VITE_DEMO_MODE", "true");
    for (const path of ["/", "/zone/Z1", "/climate", "/alerts", "/history", "/simulator"]) {
      const { unmount } = renderAt(path);
      expect(await screen.findByRole("note")).toHaveTextContent(BANNER);
      unmount();
    }
  });
});

describe("demo mode off", () => {
  it("has no picker and says real sign-in is coming", async () => {
    vi.stubEnv("VITE_DEMO_MODE", "false");
    renderAt("/sign-in", { role: null });
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.getByText(/Sign-in with your own account is coming soon/)).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(screen.queryByText(BANNER)).not.toBeInTheDocument();
  });

  it("sends a signed-out person to that sign-in page", async () => {
    vi.stubEnv("VITE_DEMO_MODE", "false");
    renderAt("/", { role: null });
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Owner/ })).not.toBeInTheDocument();
  });

  it("doesn't let a role saved by a demo build sign anyone in", async () => {
    localStorage.setItem("snm.role", "Owner");
    vi.stubEnv("VITE_DEMO_MODE", "false");
    renderFromStorage("/");
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
  });

  it("shows no banner on the app screens", async () => {
    vi.stubEnv("VITE_DEMO_MODE", "false");
    renderAt("/");
    expect(await screen.findByRole("heading", { name: "Overview" })).toBeInTheDocument();
    expect(screen.queryByText(BANNER)).not.toBeInTheDocument();
  });
});

describe("sign-in card", () => {
  it.each(["true", "false"])("no longer says “Control portal · Phase 2” (demo %s)", async (flag) => {
    vi.stubEnv("VITE_DEMO_MODE", flag);
    renderAt("/sign-in", { role: null });
    expect(await screen.findByText("Smart Nutrient Management")).toBeInTheDocument();
    expect(screen.queryByText(/Control portal · Phase 2/)).not.toBeInTheDocument();
  });
});
