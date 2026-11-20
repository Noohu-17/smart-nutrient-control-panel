import { act, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ServerAlert } from "../../api/types";
import { makeQueryClient } from "../../app/queryClient";
import { server } from "../../mocks/node";
import { fakeSignIn } from "../../mocks/session";
import { renderAt } from "../../test/render";
import { WARNING_FADE_MS } from "./notify";
import { forgetPrefs, savePrefs } from "./prefs";

/* Notifications in the app: a toast for each new alert, and a browser notification while the
   tab is in the background, for the severities each person chose. */

const alert = (id: string, severity: ServerAlert["severity"] = "CRITICAL", zone = "Z1"): ServerAlert => ({
  id,
  kind: "EC_HIGH",
  severity,
  status: "OPEN",
  zone,
  message: `EC is 2.8 mS/cm (${id})`,
  systemAction: "No automatic action",
  nextStep: "Dilute with fresh water",
  raisedAt: "2026-11-19T10:00:00.000Z",
  simTs: null,
  clearedAt: null,
  acknowledgedAt: null,
  acknowledgedBy: null,
});

let alerts: ServerAlert[] = [];
const acked: string[] = [];

beforeEach(() => {
  alerts = [];
  acked.length = 0;
  window.localStorage.clear();
  forgetPrefs();
  server.use(
    http.get("/api/alerts", () => HttpResponse.json({ items: alerts })),
    http.post("/api/alerts/:id/ack", ({ params }) => {
      acked.push(String(params.id));
      alerts = alerts.map((a) => (a.id === params.id ? { ...a, status: "ACKNOWLEDGED" } : a));
      return HttpResponse.json(alerts.find((a) => a.id === params.id));
    }),
  );
});

/** Open the app with no alerts, then raise these, as the next poll would see them. Waits for the
    farm (zone names) and the first, empty, alerts answer: what is open then doesn't pop up. */
async function raise(...raised: ServerAlert[]) {
  const queryClient = makeQueryClient({ retry: false });
  renderAt("/climate", { queryClient });
  const menu = within(screen.getByRole("navigation", { name: "Main" }));
  await menu.findByRole("link", { name: "Zone A" });
  await vi.waitFor(() => expect(queryClient.getQueryState(["alerts"])?.status).toBe("success"));
  // let the toasts take that first answer before the next one comes
  await act(() => new Promise((resolve) => setTimeout(resolve, 0)));
  alerts = raised;
  await act(() => queryClient.invalidateQueries({ queryKey: ["alerts"] }));
  return queryClient;
}

const toasts = () => within(screen.getByRole("region", { name: "New alerts" }));

describe("notifications in the app", () => {
  it("pops up a new critical alert at once, with what to do", async () => {
    await raise(alert("a1"));
    const shown = await (await toastsFound()).findByRole("alert");
    expect(shown).toHaveTextContent("Critical: Zone A · EC is 2.8 mS/cm (a1)");
    expect(shown).toHaveTextContent("Dilute with fresh water");
  });

  it("doesn't pop up alerts already open when the app starts", async () => {
    alerts = [alert("old")];
    renderAt("/climate");
    expect(await screen.findByText(/EC is 2.8 mS\/cm \(old\)/)).toBeInTheDocument(); // in the strip
    expect(screen.queryByRole("region", { name: "New alerts" })).not.toBeInTheDocument();
  });

  it("leaves warnings out until the person chooses them", async () => {
    await raise(alert("w1", "WARNING"), alert("c1"));
    await screen.findByRole("region", { name: "New alerts" });
    expect(toasts().getAllByRole("listitem")).toHaveLength(1);
    expect(toasts().queryByText(/\(w1\)/)).not.toBeInTheDocument();
  });

  it("fades a warning after a few seconds; a critical one stays", async () => {
    const me = fakeSignIn("OWNER")!;
    savePrefs(me.user.id, { severities: ["CRITICAL", "WARNING"], browser: false });
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      await raise(alert("w1", "WARNING"), alert("c1"));
      await screen.findByRole("region", { name: "New alerts" });
      expect(toasts().getByRole("status")).toHaveTextContent("Warning: Zone A · EC is 2.8 mS/cm (w1)");
      await act(() => vi.advanceTimersByTimeAsync(WARNING_FADE_MS + 100));
      expect(toasts().queryByRole("status")).not.toBeInTheDocument();
      expect(toasts().getByRole("alert")).toHaveTextContent("(c1)");
    } finally {
      vi.useRealTimers();
    }
  });

  it("opens the zone when clicked", async () => {
    await raise(alert("a1"));
    const user = userEvent.setup();
    await user.click(
      await within(await screen.findByRole("region", { name: "New alerts" })).findByRole("button", {
        name: /^Critical: Zone A/,
      }),
    );
    expect(await screen.findByRole("heading", { level: 1, name: "Zone A" })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "New alerts" })).not.toBeInTheDocument();
  });

  it("opens the Alerts page for an alert of no zone", async () => {
    await raise({ ...alert("cc"), zone: null });
    const user = userEvent.setup();
    await user.click(
      await within(await screen.findByRole("region", { name: "New alerts" })).findByRole("button", {
        name: /^Critical: EC/,
      }),
    );
    expect(await screen.findByRole("heading", { level: 1, name: "Alerts" })).toBeInTheDocument();
  });

  it("shows at most three at once, then +N more", async () => {
    await raise(alert("a1"), alert("a2"), alert("a3"), alert("a4", "CRITICAL", "Z2"), alert("a5"));
    await screen.findByRole("region", { name: "New alerts" });
    expect(toasts().getAllByRole("listitem")).toHaveLength(3);
    expect(toasts().getByRole("link", { name: "+2 more" })).toHaveAttribute("href", "/alerts");
  });

  it("is dismissed with ×", async () => {
    await raise(alert("a1"));
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: /Dismiss: Critical: Zone A/ }));
    expect(screen.queryByRole("region", { name: "New alerts" })).not.toBeInTheDocument();
  });

  it("acknowledges from the toast, which takes it away", async () => {
    await raise(alert("a1"));
    const user = userEvent.setup();
    await user.click(await toastsFound().then((t) => t.findByRole("button", { name: "Acknowledge" })));
    await vi.waitFor(() =>
      expect(screen.queryByRole("region", { name: "New alerts" })).not.toBeInTheDocument(),
    );
    expect(acked).toEqual(["a1"]);
  });
});

async function toastsFound() {
  return within(await screen.findByRole("region", { name: "New alerts" }));
}

describe("browser notifications", () => {
  const shown: { title: string; options: NotificationOptions }[] = [];
  const requestPermission = vi.fn(async () => {
    FakeNotification.permission = "granted";
    return "granted" as NotificationPermission;
  });
  class FakeNotification {
    static permission: NotificationPermission = "default";
    static requestPermission = requestPermission;
    onclick: (() => void) | null = null;
    constructor(title: string, options: NotificationOptions) {
      shown.push({ title, options });
    }
    close() {}
  }

  beforeEach(() => {
    shown.length = 0;
    requestPermission.mockClear();
    FakeNotification.permission = "default";
    vi.stubGlobal("Notification", FakeNotification);
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
  });

  it("never asks for permission when the app loads", async () => {
    renderAt("/alerts");
    await screen.findByRole("heading", { level: 2, name: "Notifications" });
    expect(requestPermission).not.toHaveBeenCalled();
    expect(screen.getByRole("checkbox", { name: /Browser notifications/ })).not.toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Critical" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Warning" })).not.toBeChecked();
  });

  it("asks once they are switched on, and keeps the choice for this person", async () => {
    renderAt("/alerts");
    const user = userEvent.setup();
    const box = await screen.findByRole("checkbox", { name: /Browser notifications/ });
    await user.click(box);
    expect(requestPermission).toHaveBeenCalledTimes(1);
    expect(box).toBeChecked();
    await user.click(screen.getByRole("checkbox", { name: "Warning" }));
    const me = fakeSignIn("OWNER")!;
    expect(JSON.parse(window.localStorage.getItem(`snm.notify.${me.user.id}`)!)).toEqual({
      severities: ["CRITICAL", "WARNING"],
      browser: true,
    });
  });

  it("keeps critical on: its box can't be unticked, and an older saved choice without it gets it back", async () => {
    const me = fakeSignIn("OWNER")!;
    window.localStorage.setItem(
      `snm.notify.${me.user.id}`,
      JSON.stringify({ severities: ["WARNING"], browser: false }),
    );
    renderAt("/alerts");
    const critical = await screen.findByRole("checkbox", { name: "Critical" });
    expect(critical).toBeChecked();
    expect(critical).toBeDisabled();
    expect(critical).toHaveAccessibleDescription(/Critical alerts always pop up/);
    expect(screen.getByRole("checkbox", { name: "Warning" })).toBeChecked();
    await userEvent.setup().click(screen.getByRole("checkbox", { name: "Warning" }));
    expect(critical).toBeChecked();
    expect(JSON.parse(window.localStorage.getItem(`snm.notify.${me.user.id}`)!).severities).toEqual([
      "CRITICAL",
    ]);
  });

  it("says so when the browser blocks them", async () => {
    requestPermission.mockImplementationOnce(async () => "denied");
    renderAt("/alerts");
    const user = userEvent.setup();
    const box = await screen.findByRole("checkbox", { name: /Browser notifications/ });
    await user.click(box);
    expect(box).not.toBeChecked();
    expect(screen.getByRole("alert")).toHaveTextContent("Notifications are blocked for this site");
  });

  it("notifies from a tab in the background, when switched on", async () => {
    FakeNotification.permission = "granted";
    const me = fakeSignIn("OWNER")!;
    savePrefs(me.user.id, { severities: ["CRITICAL"], browser: true });
    Object.defineProperty(document, "visibilityState", { configurable: true, value: "hidden" });
    await raise(alert("a1"));
    await vi.waitFor(() => expect(shown).toHaveLength(1));
    expect(shown[0].title).toBe("Critical: Zone A · EC is 2.8 mS/cm (a1)");
    expect(shown[0].options).toMatchObject({ body: "Dilute with fresh water", tag: "a1" });
  });

  it("doesn't notify while the tab is in front (the toast is enough), or when switched off", async () => {
    FakeNotification.permission = "granted";
    const me = fakeSignIn("OWNER")!;
    savePrefs(me.user.id, { severities: ["CRITICAL"], browser: true });
    await raise(alert("a1"));
    await screen.findByRole("region", { name: "New alerts" });
    expect(shown).toHaveLength(0);
  });
});
