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
    expect(await screen.findByText("Sent — waiting for the farm.")).toBeInTheDocument();
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

  it("goes back to the amounts with Edit quantities", async () => {
    const { user } = await enter({ "Stock A": "120" });
    await user.click(screen.getByRole("button", { name: "Review dose" }));
    await user.click(await screen.findByRole("button", { name: "Edit quantities" }));
    expect(screen.getByLabelText("Stock A")).toHaveValue(120);
  });
});
