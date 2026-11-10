import { screen, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import type { Device } from "../../api/types";
import { server } from "../../mocks/node";
import { renderAt } from "../../test/render";
import { lastSeenText } from "../../config/devices";

// the fake API waits 300 ms per call, so allow more than the 1 s default
const WAIT = { timeout: 3000 };

const row = async (deviceId: string) => within(await screen.findByRole("listitem", { name: deviceId }, WAIT));

const minutesAgo = (minutes: number) => new Date(Date.now() - minutes * 60_000).toISOString();

describe("Devices", () => {
  it("lists every device from GET /api/devices with its kind and zone name", async () => {
    renderAt("/devices");
    const zc2 = await row("ZC2");
    expect(zc2.getByText("ZC2")).toBeInTheDocument();
    expect(zc2.getByText(/Zone controller/)).toHaveTextContent("Zone controller · Zone B");
    expect(zc2.getByText("Online")).toBeInTheDocument();
    expect((await row("CC1")).getByText("Climate controller")).toBeInTheDocument();
    expect(screen.getAllByRole("listitem").map((li) => li.getAttribute("aria-label"))).toEqual([
      "CC1",
      "GW1",
      "PM1",
      "ZC1",
      "ZC2",
    ]);
    expect(screen.getByText("5 of 5 online")).toBeInTheDocument();
  });

  it("shows a silent controller as Offline with when it was last seen", async () => {
    // contract §14: offline after 3 minutes without any message
    server.use(
      http.get("/api/devices", () =>
        HttpResponse.json({
          items: [
            { deviceId: "ZC1", kind: "ZONE_CONTROLLER", zone: "Z1", online: true, lastSeen: minutesAgo(0) },
            { deviceId: "ZC2", kind: "ZONE_CONTROLLER", zone: "Z2", online: false, lastSeen: minutesAgo(4) },
          ] satisfies Device[],
          nextCursor: null,
        }),
      ),
    );
    renderAt("/devices");
    const zc2 = await row("ZC2");
    expect(zc2.getByText("Offline")).toBeInTheDocument();
    expect(zc2.getByText("Last seen 4 min ago")).toBeInTheDocument();
    expect(screen.getByText("1 of 2 online")).toBeInTheDocument();
  });

  it("follows nextCursor so a long list arrives whole", async () => {
    const device = (deviceId: string): Device => ({
      deviceId,
      kind: "ZONE_CONTROLLER",
      zone: null,
      online: true,
      lastSeen: null,
    });
    server.use(
      http.get("/api/devices", ({ request }) =>
        new URL(request.url).searchParams.get("cursor") === "ZC2"
          ? HttpResponse.json({ items: [device("ZC3")], nextCursor: null })
          : HttpResponse.json({ items: [device("ZC1"), device("ZC2")], nextCursor: "ZC2" }),
      ),
    );
    renderAt("/devices");
    expect((await row("ZC3")).getByText("Never heard from")).toBeInTheDocument();
    expect(screen.getAllByRole("listitem")).toHaveLength(3);
  });

  it("says so when the devices can't be loaded", async () => {
    server.use(
      http.get("/api/devices", () =>
        HttpResponse.json(
          { error: { code: "SERVICE_UNAVAILABLE", message: "The database is not reachable" } },
          { status: 503 },
        ),
      ),
    );
    renderAt("/devices");
    expect(await screen.findByText("Couldn’t load the devices", undefined, WAIT)).toBeInTheDocument();
    expect(screen.getByText("The database is not reachable")).toBeInTheDocument();
  });

  it("is in the System menu", async () => {
    renderAt("/");
    const main = within(screen.getByRole("navigation", { name: "Main" }));
    expect(await main.findByRole("link", { name: "Devices" })).toHaveAttribute("href", "/devices");
  });
});

describe("lastSeenText", () => {
  const now = Date.parse("2026-11-04T06:00:00.000Z");
  it.each([
    [null, "Never heard from"],
    ["2026-11-04T05:59:30.000Z", "Just now"],
    ["2026-11-04T05:56:00.000Z", "4 min ago"],
    ["2026-11-04T03:00:00.000Z", "3 h ago"],
  ])("%s → %s", (lastSeen, text) => {
    expect(lastSeenText(lastSeen, now)).toBe(text);
  });
});
