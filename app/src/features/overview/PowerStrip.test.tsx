// T2-15 done-when: the power strip shows fake data, with no generator item when the layout has none
import { screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { afterEach, describe, expect, it } from "vitest";
import type { PowerStatus } from "../../api/types";
import { server } from "../../mocks/node";
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

  it("says No data while the power monitor has never reported (§11.2: every value null)", async () => {
    window.history.replaceState(null, "", "/?mock=power-never");
    renderAt("/");
    const power = await strip();
    for (const name of ["Mains", "Generator", "UPS"]) {
      const item = await within(power).findByRole("group", { name });
      await waitFor(() => expect(item).toHaveTextContent("No data"));
    }
  });

  it("marks the last values once the monitor is offline, never as the power now", async () => {
    server.use(
      http.get("/api/power", () =>
        HttpResponse.json<PowerStatus>({
          online: false,
          lastSeen: "2026-11-09T05:02:11.000Z",
          ts: "2026-11-09T05:02:11.000Z",
          mains: "OK",
          phases: { l1V: 231, l2V: 229, l3V: 233 },
          source: "MAINS",
          generator: "STOPPED",
          ats: "MAINS",
          ups: { onBattery: false, batteryPct: 100, runtimeMin: 42 },
          quality: { l1V: "STALE", l2V: "STALE", l3V: "STALE", upsBatteryPct: "STALE", upsRuntimeMin: "STALE" },
        }),
      ),
    );
    renderAt("/");
    const power = await strip();
    const mains = await within(power).findByRole("group", { name: "Mains" });
    await waitFor(() => expect(mains).toHaveTextContent("231 / 229 / 233 V · No fresh sample"));
    expect(within(power).getByRole("group", { name: "UPS" })).toHaveTextContent("100% · 42 min · No fresh sample");
    expect(within(power).getByRole("group", { name: "Generator" })).toHaveTextContent("No fresh sample");
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
