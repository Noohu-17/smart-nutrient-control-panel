import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import type { ServerAlert } from "../../api/types";
import { server } from "../../mocks/node";
import { csrfHeaders } from "../../mocks/session";
import { renderAt } from "../../test/render";

const EC_HIGH: ServerAlert = {
  id: "a1",
  kind: "EC_HIGH",
  severity: "CRITICAL",
  status: "OPEN",
  zone: "Z1",
  message: "EC in Zone A is 2.8 mS/cm, above the 2.4 alarm limit",
  systemAction: "No automatic action: the farm keeps running as it was",
  nextStep: "Don't add nutrients; dilute with fresh water and check the EC probe",
  raisedAt: "2026-11-12T05:10:04.000Z",
  simTs: null,
  clearedAt: null,
  acknowledgedAt: null,
  acknowledgedBy: null,
};

const serve = (items: ServerAlert[]) =>
  server.use(http.get("/api/alerts", () => HttpResponse.json({ items })));
const strip = async () => within(await screen.findByRole("region", { name: "Alerts" }));

describe("server alerts in the app (T2-22)", () => {
  it("shows a server alert on the Overview, with what to do", async () => {
    serve([EC_HIGH]);
    renderAt("/");
    const alert = await (await strip()).findByRole("alert");
    expect(alert).toHaveTextContent("EC in Zone A is 2.8 mS/cm, above the 2.4 alarm limit.");
    expect(alert).toHaveTextContent("dilute with fresh water and check the EC probe.");
    expect(alert).toHaveClass("crit");
  });

  it("shows it on its zone's page", async () => {
    serve([EC_HIGH]);
    renderAt("/zone/Z1");
    expect(await (await strip()).findByText(/EC in Zone A is 2.8/)).toBeInTheDocument();
  });

  it("shows it on another zone's page too, with a link to its zone", async () => {
    serve([EC_HIGH]);
    renderAt("/zone/Z2");
    const s = await strip();
    expect(await s.findByText(/EC in Zone A is 2.8/)).toBeInTheDocument();
    expect(s.getByRole("link", { name: "Open Zone A" })).toHaveAttribute("href", "/zone/Z1");
  });

  it("shows it on pages with no strip of their own, with a link to the zone", async () => {
    serve([EC_HIGH]);
    renderAt("/history");
    const s = await strip();
    expect(await s.findByText(/EC in Zone A is 2.8/)).toBeInTheDocument();
    expect(s.getByRole("link", { name: "Open Zone A" })).toHaveAttribute("href", "/zone/Z1");
  });

  it("lists a cleared alert under Cleared, and none needs attention", async () => {
    serve([{ ...EC_HIGH, status: "CLEARED", clearedAt: "2026-11-12T05:20:00.000Z" }]);
    renderAt("/alerts");
    const cleared = await screen.findByRole("list", { name: "Cleared alerts" });
    expect(cleared).toHaveTextContent("EC in Zone A is 2.8");
    expect(screen.getByText("No open alerts")).toBeInTheDocument();
    expect(within(cleared).queryByRole("button", { name: "Acknowledge" })).not.toBeInTheDocument();
  });

  it("Acknowledge goes with the session's CSRF token and shows who acknowledged it", async () => {
    let ackedBy: string | null = null;
    server.use(
      http.get("/api/alerts", () =>
        HttpResponse.json({
          items: [
            ackedBy
              ? {
                  ...EC_HIGH,
                  status: "ACKNOWLEDGED",
                  acknowledgedAt: "2026-11-12T05:12:00.000Z",
                  acknowledgedBy: ackedBy,
                }
              : EC_HIGH,
          ],
        }),
      ),
      http.post("/api/alerts/a1/ack", ({ request }) => {
        // the server knows who from the session (T1-35); the app sends only the token
        expect(request.headers.get("X-CSRF-Token")).toBe(csrfHeaders()["X-CSRF-Token"]);
        ackedBy = "Demo Operator";
        return HttpResponse.json({ ...EC_HIGH, status: "ACKNOWLEDGED", acknowledgedBy: ackedBy });
      }),
    );
    renderAt("/alerts", { role: "Operator" });
    const list = within(await screen.findByRole("list", { name: "Current alerts" }));
    await userEvent.click(list.getByRole("button", { name: "Acknowledge" }));
    expect(await list.findByText(/Acknowledged by Demo Operator/)).toBeInTheDocument();
    expect(ackedBy).toBe("Demo Operator");
    expect(list.queryByRole("button", { name: "Acknowledge" })).not.toBeInTheDocument();
  });

  it("locks Acknowledge for a Viewer", async () => {
    serve([EC_HIGH]);
    renderAt("/alerts", { role: "Viewer" });
    const list = within(await screen.findByRole("list", { name: "Current alerts" }));
    expect(list.getByRole("button", { name: "Acknowledge" })).toBeDisabled();
  });
});
