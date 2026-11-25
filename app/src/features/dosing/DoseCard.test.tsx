import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import type { DoseRequest } from "../../api/types";
import type { Role } from "../../auth/roles";
import { farmLayout } from "../../mocks/data/layout";
import { server } from "../../mocks/node";
import { renderAt } from "../../test/render";

async function enter(amounts: Record<string, string>, { role = "Operator" as Role } = {}) {
  const user = userEvent.setup();
  renderAt("/zone/Z1", { role });
  const card = within((await screen.findByRole("heading", { name: "Manual dose" })).closest("section")!);
  for (const [label, ml] of Object.entries(amounts)) await user.type(card.getByLabelText(label), ml);
  return { user, card };
}

const checks = () => within(screen.getByRole("list", { name: "Safety checks" }));
const startButton = () => screen.getByRole("button", { name: "Start dose" });

describe("Manual dose and review", () => {
  it("shows one amount field per dosing line in the zone's config", async () => {
    server.use(
      http.get("/api/farm", () => {
        const [first, ...rest] = farmLayout.zones;
        return HttpResponse.json({
          ...farmLayout,
          zones: [{ ...first, doseLines: ["A", "B"] }, ...rest],
        });
      }),
    );
    const { card } = await enter({});
    expect(card.getAllByRole("spinbutton")).toHaveLength(2);
    expect(card.queryByLabelText("Acid")).not.toBeInTheDocument();
  });

  it("only allows Review when there is at least one whole-mL amount", async () => {
    const { user, card } = await enter({});
    const review = card.getByRole("button", { name: "Review dose" });
    expect(review).toBeDisabled();
    await user.type(card.getByLabelText("Stock A"), "12.5");
    expect(review).toBeDisabled();
    await user.clear(card.getByLabelText("Stock A"));
    await user.type(card.getByLabelText("Stock A"), "120");
    expect(review).toBeEnabled();
  });

  it("locks the dose for a Viewer, with the reason", async () => {
    const { card } = await enter({}, { role: "Viewer" });
    expect(card.getByLabelText("Stock A")).toBeDisabled();
    expect(card.getByRole("button", { name: "Review dose" })).toHaveAccessibleDescription(
      "Viewers can’t operate equipment",
    );
  });

  it("lists the §11.3 checks and starts an allowed dose only after the box is ticked", async () => {
    const { user } = await enter({ "Stock A": "120", "Stock B": "120", Acid: "30" });
    await user.click(screen.getByRole("button", { name: "Review dose" }));

    expect(await screen.findByRole("heading", { name: "Review safety checks" })).toBeInTheDocument();
    expect(checks().getAllByRole("listitem")).toHaveLength(11);
    expect(checks().getAllByText("Pass")).toHaveLength(11);

    expect(startButton()).toBeDisabled();
    await user.click(screen.getByRole("checkbox", { name: /I have checked the quantities/ }));
    expect(startButton()).toBeEnabled();
    await user.click(startButton());
    expect(await screen.findByText("Sent — waiting for the farm")).toBeInTheDocument();
  });

  it("shows each line's limits under its amount, from GET /api/zones/Z1/dose-limits", async () => {
    const { card } = await enter({});
    // limits.json: 300 mL per dose and 1,500 a day of A and B, 100 and 400 of acid; the fake API
    // has 240 mL of A and B and 60 mL of acid dosed today
    await card.findByText("Max 100 mL per dose · 340 mL left today");
    for (const label of ["Stock A", "Stock B"])
      expect(card.getByLabelText(label)).toHaveAccessibleDescription(
        /^Max 300 mL per dose · 1,260 mL left today /,
      );
    expect(card.getByLabelText("Acid")).toHaveAccessibleDescription(
      "Max 100 mL per dose · 340 mL left today Acid (pH down) · Phosphoric acid 10% · Lowers pH",
    );
  });

  it("turns the box red as soon as the amount is over a limit, and Review still checks", async () => {
    const { user, card } = await enter({});
    const stockA = card.getByLabelText("Stock A");
    await card.findByText("Max 100 mL per dose · 340 mL left today"); // the limits have loaded
    await user.type(stockA, "300");
    expect(stockA).toHaveAttribute("aria-invalid", "false");
    await user.type(stockA, "1"); // 3001
    expect(stockA).toHaveAttribute("aria-invalid", "true");
    expect(stockA).toHaveAccessibleDescription(/^Over the 300 mL per-dose limit /);
    // only a hint: the review is still the one that decides
    expect(card.getByRole("button", { name: "Review dose" })).toBeEnabled();
  });

  it("says when an amount is over what's left today", async () => {
    server.use(
      http.get("/api/zones/:zoneId/dose-limits", () =>
        HttpResponse.json({
          zone: "Z1",
          lines: [{ line: "A", maxPerDoseMl: 300, max24hMl: 1500, dosed24hMl: 1400, left24hMl: 100 }],
        }),
      ),
    );
    const { user, card } = await enter({});
    const stockA = card.getByLabelText("Stock A");
    await card.findByText("Max 300 mL per dose · 100 mL left today");
    await user.type(stockA, "150");
    expect(stockA).toHaveAttribute("aria-invalid", "true");
    expect(stockA).toHaveAccessibleDescription("Over what's left today: 100 mL"); // a back end from before T1-38: no nutrient line
  });

  it("works as before, without hints, when the limits can't be loaded", async () => {
    server.use(http.get("/api/zones/:zoneId/dose-limits", () => HttpResponse.json({}, { status: 503 })));
    const { user, card } = await enter({});
    await user.type(card.getByLabelText("Stock A"), "400");
    expect(card.queryByText(/per dose/)).not.toBeInTheDocument();
    expect(card.getByRole("button", { name: "Review dose" })).toBeEnabled();
  });

  it("shows Error and why for a dose over the per-dose limit, with the limit from the fake API", async () => {
    const { user } = await enter({ "Stock A": "400" });
    await user.click(screen.getByRole("button", { name: "Review dose" }));

    const row = (await screen.findByText("Within per-dose limits")).closest("li")!;
    expect(row).toHaveTextContent("Line A exceeds 300 mL per dose");
    expect(within(row).getByText("Error")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("Dose error.");
    expect(screen.queryByText(/block/i)).not.toBeInTheDocument();
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
    expect(startButton()).toBeDisabled();
  });

  it("asks the user to confirm the warnings when a check warns", async () => {
    server.use(
      http.post("/api/dose-requests", () =>
        HttpResponse.json<DoseRequest>(
          {
            id: "dr_warn",
            zone: "Z1",
            lines: [{ line: "A", ml: 120 }],
            projected: { ec: 1.7, ph: 6.0 },
            checks: [{ id: "DAYTANK", result: "WARN", message: "Day-tank B at 18%, refill soon" }],
            estimate: { durationMin: 7, totalMl: 120 },
            canConfirm: true,
          },
          { status: 201 },
        ),
      ),
    );
    const { user } = await enter({ "Stock A": "120" });
    await user.click(screen.getByRole("button", { name: "Review dose" }));

    expect(await screen.findByText("Day-tank B at 18%, refill soon")).toBeInTheDocument();
    expect(checks().getByText("Warn")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: /and the warnings above/ })).toBeInTheDocument();
  });

  it("sends only the lines with an amount, so the farm never gets a 0 mL line", async () => {
    const sent: unknown[] = [];
    server.use(
      http.post("/api/dose-requests", async ({ request }) => {
        sent.push(await request.clone().json());
        return undefined; // on to the fake API
      }),
    );
    const { user } = await enter({ "Stock A": "120", Acid: "0" });
    await user.click(screen.getByRole("button", { name: "Review dose" }));

    expect(await screen.findByRole("heading", { name: "Review safety checks" })).toBeInTheDocument();
    expect(sent).toEqual([{ zone: "Z1", lines: [{ line: "A", ml: 120 }] }]);
  });

  it("goes back to the amounts with Edit quantities", async () => {
    const { user } = await enter({ "Stock A": "120" });
    await user.click(screen.getByRole("button", { name: "Review dose" }));
    await user.click(await screen.findByRole("button", { name: "Edit quantities" }));
    expect(screen.getByLabelText("Stock A")).toHaveValue(120);
  });
});

describe("Stock details", () => {
  it("shows what each stock is, until the eye button hides it; the choice is remembered", async () => {
    const { user, card } = await enter({});
    const stock = /Calcium nitrate and iron chelate/;
    expect(await card.findByText(stock)).toBeInTheDocument();
    await user.click(card.getByRole("button", { name: "Hide stock" }));
    expect(card.queryByText(stock)).not.toBeInTheDocument();
    expect(localStorage.getItem("snm.showStock")).toBe("hidden");
    await user.click(card.getByRole("button", { name: "Show stock" }));
    expect(card.getByText(stock)).toBeInTheDocument();
    expect(localStorage.getItem("snm.showStock")).toBeNull();
  });
});
