import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, describe, expect, it } from "vitest";
import type { Command, DoseRequest } from "../../api/types";
import {
  addCommand,
  commandAt,
  markZoneStopped,
  setCommandStepMs,
  type CommandEnding,
  type CommandRecord,
} from "../../mocks/commands";
import { server } from "../../mocks/node";
import { renderAt } from "../../test/render";

const FAST_MS = 40;

const useScenario = (name: string) => window.sessionStorage.setItem("snm.mockScenario", name);

afterEach(() => window.sessionStorage.clear());

/** Start a real dose through the form and the review. */
async function startDose() {
  setCommandStepMs(FAST_MS);
  const user = userEvent.setup();
  renderAt("/zone/Z1", { role: "Operator" });
  const card = within((await screen.findByRole("heading", { name: "Manual dose" })).closest("section")!);
  await user.type(card.getByLabelText("Stock A"), "120");
  await user.type(card.getByLabelText("Stock B"), "120");
  await user.click(card.getByRole("button", { name: "Review dose" }));
  await user.click(await screen.findByRole("checkbox", { name: /I have checked/ }));
  await user.click(screen.getByRole("button", { name: "Start dose" }));
  return user;
}

const dr: DoseRequest = {
  id: "dr_1",
  zone: "Z1",
  lines: [
    { line: "A", ml: 120 },
    { line: "B", ml: 120 },
  ],
  projected: { ec: 1.7, ph: 6 },
  checks: [],
  estimate: { durationMin: 7, totalMl: 240 },
  canConfirm: true,
};

function timeline(ending: CommandEnding) {
  setCommandStepMs(1000);
  const record: CommandRecord = {
    cmdId: "c1",
    dr,
    device: "ZC1",
    before: { ec: 1.5, ph: 6.1 },
    ending,
    createdAt: 0,
  };
  const states = Array.from({ length: 8 }, (_, s) => commandAt(record, s * 1000).state);
  return states.filter((state, i) => state !== states[i - 1]);
}

describe("Fake command timeline", () => {
  it("moves through the §8 states in the allowed order", () => {
    expect(timeline("COMPLETED")).toEqual(["DISPATCHED", "ACCEPTED", "EXECUTING", "COMPLETED"]);
    expect(timeline("REJECTED")).toEqual(["DISPATCHED", "REJECTED"]);
    expect(timeline("EXPIRED")).toEqual(["DISPATCHED", "EXPIRED"]);
    expect(timeline("FAILED")).toEqual(["DISPATCHED", "ACCEPTED", "EXECUTING", "FAILED"]);
    expect(timeline("PARTIAL")).toEqual(["DISPATCHED", "ACCEPTED", "EXECUTING", "PARTIAL"]);
    expect(timeline("UNKNOWN")).toEqual(["DISPATCHED", "ACCEPTED", "EXECUTING", "UNKNOWN"]);
  });

  it("reports COMPLETED without after while mixing, then again with after (§6.6)", () => {
    setCommandStepMs(1000);
    const record: CommandRecord = {
      cmdId: "c2",
      dr,
      device: "ZC1",
      before: { ec: 1.5, ph: 6.1 },
      ending: "COMPLETED",
      createdAt: 0,
    };
    const lastLineEnds = commandAt(record, 4000);
    expect(lastLineEnds.state).toBe("COMPLETED");
    expect(lastLineEnds.after).toBeNull();
    expect(lastLineEnds.endedAt).not.toBeNull();
    expect(commandAt(record, 6000).after).toEqual({ ec: 1.7, ph: 6 });
  });
});

describe("Command status", () => {
  it("runs a dose to Completed with requested vs delivered and the final EC", async () => {
    await startDose();
    expect(await screen.findByText("Completed")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("re-measured");
    const lines = within(screen.getByRole("list", { name: "Dose lines" }));
    expect(lines.getAllByText("120 / 120 mL")).toHaveLength(2);
    expect(screen.queryByRole("button", { name: "Stop dose" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Done" })).toBeEnabled();
  });

  it("shows a failed dose as a device error, in plain words", async () => {
    useScenario("dose-failed");
    await startDose();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("ZC1: The pump ran but no flow was measured.");
    expect(screen.getByText("Device error")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Acknowledge" })).toBeEnabled();
    expect(screen.getByText("Skipped")).toBeInTheDocument();
  });

  it("shows a refused dose with the reason and never as running", async () => {
    useScenario("dose-blocked");
    await startDose();
    expect(await screen.findByRole("alert")).toHaveTextContent(/Refused: .*24-hour/);
    expect(screen.getByRole("button", { name: "Back to the form" })).toBeEnabled();
  });

  it("never makes Unknown look like success, and keeps Stop", async () => {
    useScenario("dose-unknown");
    await startDose();
    expect(await screen.findByText("Result unknown — farm not responding")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Nothing is confirmed.");
    expect(screen.queryByText("Completed")).not.toBeInTheDocument();
    expect(screen.queryByText("Done")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Done" })).not.toBeInTheDocument();
    expect(screen.getAllByText("Unknown").length).toBeGreaterThan(0);
    expect(document.querySelector(".cmd-lines .chip-ok")).toBeNull();
    expect(screen.getByRole("button", { name: "Stop dose" })).toBeEnabled();
  });

  it("stops a running dose and keeps the amounts delivered so far", async () => {
    const user = await startDose();
    setCommandStepMs(400);
    await user.click(await screen.findByRole("button", { name: "Stop dose" }));
    expect(await screen.findByText("Stopped")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("A stop command arrived.");
  });

  it("shows Mixing with the before values until after arrives", async () => {
    const mixing: Command = {
      cmdId: "c-mix",
      device: "ZC1",
      state: "COMPLETED",
      reason: null,
      lines: [{ line: "A", requestedMl: 120, deliveredMl: 120, state: "DONE" }],
      before: { ec: 1.5, ph: 6.1 },
      after: null,
      startedAt: "2026-11-05T10:00:00.000Z",
      endedAt: "2026-11-05T10:01:00.000Z",
    };
    server.use(http.get("/api/commands/:cmdId", () => HttpResponse.json(mixing)));
    await startDose();
    expect(await screen.findByText("Mixing")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Before: EC 1.5 · pH 6.1");
    expect(screen.queryByText("Completed")).not.toBeInTheDocument();
  });
});

describe("Stop and Resume", () => {
  it("leaves the zone stopped after a stop, until a Grower presses Resume", async () => {
    const user = await startDose();
    setCommandStepMs(400);
    await user.click(await screen.findByRole("button", { name: "Stop dose" }));
    expect(await screen.findByText("Zone A is stopped.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Resume" }));
    expect(await screen.findByRole("heading", { name: "Readings" })).toBeInTheDocument();
    await expect.poll(() => screen.queryByText("Zone A is stopped.")).toBeNull();
  });

  it("locks Resume for a Viewer, with the reason", async () => {
    markZoneStopped("Z1");
    renderAt("/zone/Z1", { role: "Viewer" });
    const resume = await screen.findByRole("button", { name: "Resume" });
    expect(resume).toBeDisabled();
    expect(screen.getByText("Viewers can’t operate equipment", { selector: ".banner .lbtn-reason" })).toBeVisible();
  });

  it("keeps Stop enabled for a Viewer while a dose runs", async () => {
    addCommand({
      cmdId: "c-viewer",
      dr,
      device: "ZC1",
      before: { ec: 1.5, ph: 6.1 },
      ending: "COMPLETED",
      createdAt: Date.now(),
    });
    renderAt("/zone/Z1", { role: "Viewer" });
    expect(await screen.findByRole("button", { name: "Stop dose" })).toBeEnabled();
  });
});
