import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import type { Operation, OperationList, ServerRole } from "../../api/types";
import { setCommandStepMs } from "../../mocks/commands";
import { server } from "../../mocks/node";
import { csrfHeaders, fakeSignIn } from "../../mocks/session";
import { hold } from "../../mocks/sim";
import { renderAt } from "../../test/render";

const FAST_MS = 5;
const json = { "Content-Type": "application/json" };

/** Each action through the fake API, as the signed-in `role`, the way the app sends it. */
async function as(role: ServerRole, path: string, body?: unknown) {
  fakeSignIn(role);
  const response = await fetch(`/api${path}`, {
    method: "POST",
    headers: { ...csrfHeaders(), ...json },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  expect(response.ok).toBe(true);
  // a moment apart, so each action has its own time and the order is the one they were done in
  await new Promise((resolve) => setTimeout(resolve, 5));
  return response.json() as Promise<Record<string, unknown>>;
}

async function dose(role: ServerRole = "OPERATOR") {
  setCommandStepMs(FAST_MS);
  const review = await as(role, "/dose-requests", {
    zone: "Z1",
    lines: [
      { line: "A", ml: 120 },
      { line: "B", ml: 120 },
    ],
  });
  return as(role, `/dose-requests/${review.id as string}/confirm`);
}

async function ack(role: ServerRole = "OWNER") {
  hold({ target: "Z1", metric: "ec", value: 2.8, gradual: false });
  await fetch("/api/zones/Z1"); // the fake API raises EC_HIGH from the zone it has seen
  const { items } = (await (await fetch("/api/alerts")).json()) as { items: { id: string }[] };
  return as(role, `/alerts/${items[0].id}/ack`);
}

const rows = async () =>
  within(await screen.findByRole("list", { name: "Operations" })).getAllByRole("listitem");

describe("History page (T2-23)", () => {
  it("lists a dose, a stop, a resume and an ack, newest first, with who did each", async () => {
    await dose("OPERATOR");
    await as("VIEWER", "/stop", { scope: "ALL_OPERATIONS" });
    await as("AGRONOMIST", "/resume", { zone: "Z1" });
    await ack("OWNER");

    renderAt("/history", { role: "Viewer" }); // every role may read it
    const items = await rows();
    expect(items).toHaveLength(4);
    expect(items[0]).toHaveTextContent("Acknowledged: EC in Zone A");
    expect(items[0]).toHaveTextContent("Zone A · Demo Owner · Owner");
    expect(items[0]).toHaveTextContent("Acknowledged");
    expect(items[1]).toHaveTextContent("Resume");
    expect(items[1]).toHaveTextContent("Zone A · Demo Agronomist · Agronomist");
    expect(items[2]).toHaveTextContent("Stop: all operations");
    expect(items[2]).toHaveTextContent("All zones · Demo Viewer · Viewer");
    expect(items[3]).toHaveTextContent("Dose");
    expect(items[3]).toHaveTextContent("Zone A · Demo Operator · Operator");
    for (const item of items) expect(within(item).getByText(/\d{2}:\d{2}/)).toBeInTheDocument(); // the time
  });

  it("shows what a dose delivered, EC and pH before and after, and its result", async () => {
    await dose();
    await new Promise((resolve) => setTimeout(resolve, 20 * FAST_MS)); // the fake dose finishes
    renderAt("/history");
    const [item] = await rows();
    expect(item).toHaveTextContent("Dose: Stock A 120 mL · Stock B 120 mL");
    expect(item).toHaveTextContent(/EC \d+(\.\d+)? → \d+(\.\d+)? · pH \d+(\.\d+)? → \d+(\.\d+)?/);
    expect(within(item).getByText("Completed")).toHaveClass("chip-ok");
  });

  it("lists a dose started from the zone page", async () => {
    setCommandStepMs(FAST_MS);
    const user = userEvent.setup();
    const { unmount } = renderAt("/zone/Z1", { role: "Operator" });
    const card = within((await screen.findByRole("heading", { name: "Manual dose" })).closest("section")!);
    await user.type(card.getByLabelText("Stock A"), "120");
    await user.type(card.getByLabelText("Stock B"), "120");
    await user.click(card.getByRole("button", { name: "Review dose" }));
    await user.click(await screen.findByRole("checkbox", { name: /I have checked/ }));
    await user.click(screen.getByRole("button", { name: "Start dose" }));
    await screen.findByText(/Dose status/);
    unmount();

    renderAt("/history", { role: "Operator" });
    const [item] = await rows();
    expect(item).toHaveTextContent("Stock A");
    expect(item).toHaveTextContent("Demo Operator · Operator");
  });

  it("filters by kind and by zone, and a site-wide stop counts for every zone", async () => {
    const user = userEvent.setup();
    await as("OWNER", "/stop", { scope: "ZONE", zone: "Z2" });
    await as("OWNER", "/stop", { scope: "ALL_OPERATIONS" });
    await as("OWNER", "/resume", { zone: "Z1" });
    renderAt("/history");
    expect(await rows()).toHaveLength(3);

    await user.selectOptions(screen.getByLabelText("Show"), "Stops");
    await screen.findByText("Stop: the zone");
    expect((await rows()).map((li) => li.querySelector("b")!.textContent)).toEqual([
      "Stop: all operations",
      "Stop: the zone",
    ]);

    await user.selectOptions(screen.getByLabelText("Show"), "Everything");
    await user.selectOptions(screen.getByLabelText("Zone"), "Zone A");
    await screen.findByText("Resume");
    expect((await rows()).map((li) => li.querySelector("b")!.textContent)).toEqual([
      "Resume",
      "Stop: all operations",
    ]);
  });

  it("loads older actions a page at a time", async () => {
    const op = (n: number): Operation => ({
      kind: "STOP",
      at: `2026-11-20T04:5${n}:00.000Z`,
      by: { id: "u1", name: "Ravi Operator", role: "OPERATOR" },
      zone: null,
      target: { scope: "ALL_OPERATIONS" },
      cmdId: `c${n}`,
      result: { state: "COMPLETED", reason: null },
      lines: null,
      before: null,
      after: null,
      alert: null,
    });
    const asked: (string | null)[] = [];
    server.use(
      http.get("/api/operations", ({ request }) => {
        const before = new URL(request.url).searchParams.get("before");
        asked.push(before);
        const page: OperationList = before
          ? { items: [op(1)], next: null }
          : { items: [op(3), op(2)], next: "2026-11-20T04:52:00.000000Z~c2" };
        return HttpResponse.json(page);
      }),
    );
    const user = userEvent.setup();
    renderAt("/history");
    expect(await rows()).toHaveLength(2);
    await user.click(screen.getByRole("button", { name: "Load older" }));
    await waitFor(() =>
      expect(within(screen.getByRole("list", { name: "Operations" })).getAllByRole("listitem")).toHaveLength(
        3,
      ),
    );
    expect(asked).toContain("2026-11-20T04:52:00.000000Z~c2");
    expect(screen.queryByRole("button", { name: "Load older" })).not.toBeInTheDocument();
  });

  it("says so when nothing has been done yet", async () => {
    renderAt("/history");
    expect(await screen.findByRole("heading", { name: "No operations yet" })).toBeInTheDocument();
  });

  it("says so when the history can't be loaded", async () => {
    server.use(
      http.get("/api/operations", () =>
        HttpResponse.json(
          { error: { code: "SERVICE_UNAVAILABLE", message: "The database is not available right now" } },
          { status: 503 },
        ),
      ),
    );
    renderAt("/history");
    expect(await screen.findByRole("heading", { name: "Couldn’t load the history" })).toBeInTheDocument();
    expect(screen.getByText("The database is not available right now")).toBeInTheDocument();
  });

  it("shows a refused dose's reason", async () => {
    server.use(
      http.get("/api/operations", () =>
        HttpResponse.json({
          items: [
            {
              kind: "DOSE",
              at: "2026-11-20T04:55:00.000Z",
              by: { id: "u1", name: null, role: null },
              zone: "Z1",
              target: { zone: "Z1" },
              cmdId: "c9",
              result: { state: "REJECTED", reason: "LIMIT_24H" },
              lines: [{ line: "A", requestedMl: 120, deliveredMl: 0, state: "SKIPPED" }],
              before: null,
              after: null,
              alert: null,
            },
          ],
          next: null,
        } satisfies OperationList),
      ),
    );
    renderAt("/history");
    await screen.findByText(/Zone A · u1/); // a user the server no longer knows, by ID
    const [item] = await rows();
    expect(item).toHaveTextContent("Dose: Stock A 0 of 120 mL");
    expect(item).toHaveTextContent("A line would pass its 24-hour limit.");
    expect(within(item).getByText("Rejected")).toHaveClass("chip-crit");
  });
});
