import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { server } from "../../mocks/node";
import { addSubscription, FAKE_PUSH_KEY, pushSubscriptions, setPushDelivery } from "../../mocks/push";
import { fakeSignInAccount } from "../../mocks/session";
import { renderAt } from "../../test/render";
import { forgetPrefs } from "./prefs";
import { keyBytes } from "./push";

/* Web Push in the app (after T1-41, §11.2): the "app closed" switch in Notifications, sign-out,
   and a click on a pushed notification. jsdom has no service workers or push, so the browser's
   side is faked here; the fake API's /api/push routes are the real shapes (mocks/push.ts). */

const ENDPOINT = "https://fcm.googleapis.com/fcm/send/this-browser";

class FakeSubscription {
  endpoint = ENDPOINT;
  options: { applicationServerKey: ArrayBuffer };
  constructor(key: ArrayBuffer) {
    this.options = { applicationServerKey: key };
  }
  toJSON() {
    return { endpoint: this.endpoint, keys: { p256dh: "p256dh-key", auth: "auth-secret" } };
  }
  unsubscribe = vi.fn(async () => {
    registration.subscription = null;
    return true;
  });
}

const registration = {
  subscription: null as FakeSubscription | null,
  installing: null,
  waiting: null,
  active: { state: "activated" },
  pushManager: {
    getSubscription: async () => registration.subscription,
    subscribe: vi.fn(
      async ({
        applicationServerKey,
      }: {
        userVisibleOnly: boolean;
        applicationServerKey: Uint8Array<ArrayBuffer>;
      }) => {
        registration.subscription = new FakeSubscription(applicationServerKey.buffer);
        return registration.subscription;
      },
    ),
  },
};
let registered = false;
let fromWorker: ((event: MessageEvent) => void) | null = null;
const serviceWorker = {
  register: vi.fn(async () => {
    registered = true;
    return registration;
  }),
  getRegistration: vi.fn(async () => (registered ? registration : undefined)),
  addEventListener: (_: string, listener: (event: MessageEvent) => void) => (fromWorker = listener),
  removeEventListener: () => (fromWorker = null),
};
const requestPermission = vi.fn(async () => {
  FakeNotification.permission = "granted";
  return "granted" as NotificationPermission;
});
class FakeNotification {
  static permission: NotificationPermission = "default";
  static requestPermission = requestPermission;
}

const appClosed = () => screen.findByRole("checkbox", { name: /when the app is closed/ });

beforeEach(() => {
  registered = false;
  registration.subscription = null;
  registration.pushManager.subscribe.mockClear();
  serviceWorker.register.mockClear();
  requestPermission.mockClear();
  FakeNotification.permission = "default";
  window.localStorage.clear();
  forgetPrefs();
  vi.stubGlobal("Notification", FakeNotification);
  vi.stubGlobal("PushManager", class {});
  Object.defineProperty(navigator, "serviceWorker", { configurable: true, value: serviceWorker });
  setPushDelivery(["IN_APP", "WEB_PUSH"]);
});
afterEach(() => {
  vi.unstubAllGlobals();
  Reflect.deleteProperty(navigator, "serviceWorker");
});

describe("notifications with the app closed (Web Push)", () => {
  it("isn't offered on a site that doesn't push alerts (GET /push/key is 404)", async () => {
    setPushDelivery(["IN_APP"]);
    renderAt("/alerts", { account: "u-agronomist" });
    await screen.findByRole("heading", { level: 2, name: "Notifications" });
    await waitFor(() =>
      expect(screen.queryByRole("checkbox", { name: /when the app is closed/ })).not.toBeInTheDocument(),
    );
    expect(serviceWorker.register).not.toHaveBeenCalled();
  });

  it("turns on: asks the browser, subscribes with the server's key, and the server gets critical alerts", async () => {
    const user = userEvent.setup();
    renderAt("/alerts", { account: "u-agronomist" });
    const box = await appClosed();
    await waitFor(() => expect(box).toBeEnabled());
    expect(requestPermission).not.toHaveBeenCalled(); // never when the page opens
    await user.click(box);

    await waitFor(() => expect(box).toBeChecked());
    expect(requestPermission).toHaveBeenCalledTimes(1);
    expect(serviceWorker.register).toHaveBeenCalledWith("/push/sw.js", { scope: "/push/" });
    const [{ applicationServerKey, userVisibleOnly }] = registration.pushManager.subscribe.mock.calls[0];
    expect(userVisibleOnly).toBe(true);
    expect([...applicationServerKey]).toEqual([...keyBytes(FAKE_PUSH_KEY)]);
    expect(pushSubscriptions().get(ENDPOINT)).toEqual({ userId: "u-agronomist", severities: ["CRITICAL"] });

    // warnings too, once ticked: the same browser, sent again
    await user.click(screen.getByRole("checkbox", { name: "Warning" }));
    await waitFor(() =>
      expect(pushSubscriptions().get(ENDPOINT)?.severities).toEqual(["CRITICAL", "WARNING"]),
    );
  });

  it("always pushes critical alerts, even when only warnings are asked for (as the server does)", async () => {
    fakeSignInAccount("u-agronomist");
    const answer = addSubscription({
      endpoint: ENDPOINT,
      keys: { p256dh: "k", auth: "a" },
      severities: ["WARNING"],
    });
    expect(await answer.json()).toEqual({ endpoint: ENDPOINT, severities: ["CRITICAL", "WARNING"] });
  });

  it("turns off: the server forgets this browser, and so does the browser", async () => {
    const user = userEvent.setup();
    renderAt("/alerts", { account: "u-agronomist" });
    const box = await appClosed();
    await waitFor(() => expect(box).toBeEnabled());
    await user.click(box);
    await waitFor(() => expect(box).toBeChecked());
    const subscription = registration.subscription!;

    await user.click(box);
    await waitFor(() => expect(box).not.toBeChecked());
    expect(pushSubscriptions().size).toBe(0);
    expect(subscription.unsubscribe).toHaveBeenCalled();
  });

  it("tells a demo user why not (403), and the browser doesn't keep the subscription", async () => {
    const user = userEvent.setup();
    renderAt("/alerts", { role: "Owner" }); // the demo Owner
    const box = await appClosed();
    await waitFor(() => expect(box).toBeEnabled());
    await user.click(box);

    expect(await screen.findByText(/Demo users can't turn on notifications/)).toBeInTheDocument();
    expect(box).not.toBeChecked();
    expect(registration.subscription).toBeNull();
    expect(pushSubscriptions().size).toBe(0);
  });

  it("says so when the server has no key yet (503), and can't be switched on", async () => {
    server.use(
      http.get("/api/push/key", () =>
        HttpResponse.json(
          {
            error: { code: "SERVICE_UNAVAILABLE", message: "Notifications aren't set up on the server yet" },
          },
          { status: 503 },
        ),
      ),
    );
    renderAt("/alerts", { account: "u-agronomist" });
    const box = await appClosed();
    await waitFor(() =>
      expect(box).toHaveAccessibleDescription("Notifications aren’t set up on the server yet."),
    );
    expect(box).toBeDisabled();
  });

  it("is already on when this browser subscribed before", async () => {
    registered = true;
    registration.subscription = new FakeSubscription(keyBytes(FAKE_PUSH_KEY).buffer);
    window.localStorage.setItem("snm.push.owner", "u-agronomist");
    renderAt("/alerts", { account: "u-agronomist" });
    const box = await appClosed();
    await waitFor(() => expect(box).toBeChecked());
    // and the server is told what this person picks now
    await waitFor(() => expect(pushSubscriptions().get(ENDPOINT)?.severities).toEqual(["CRITICAL"]));
    expect(registration.pushManager.subscribe).not.toHaveBeenCalled();
  });

  it("subscribes again, without asking, when the server's key has changed since this browser subscribed", async () => {
    registered = true;
    FakeNotification.permission = "granted";
    const old = new FakeSubscription(new Uint8Array([4, 1, 2, 3]).buffer);
    registration.subscription = old;
    window.localStorage.setItem("snm.push.owner", "u-agronomist");
    renderAt("/alerts", { account: "u-agronomist" });
    const box = await appClosed();
    await waitFor(() => expect(box).toBeChecked());

    await waitFor(() => expect(registration.pushManager.subscribe).toHaveBeenCalledTimes(1));
    const [{ applicationServerKey }] = registration.pushManager.subscribe.mock.calls[0];
    expect([...applicationServerKey]).toEqual([...keyBytes(FAKE_PUSH_KEY)]);
    expect(old.unsubscribe).toHaveBeenCalled();
    expect(requestPermission).not.toHaveBeenCalled();
    await waitFor(() => expect(pushSubscriptions().get(ENDPOINT)?.severities).toEqual(["CRITICAL"]));
  });

  it("says so when the ticked severities can't be saved, and tries again on the next change", async () => {
    registered = true;
    registration.subscription = new FakeSubscription(keyBytes(FAKE_PUSH_KEY).buffer);
    window.localStorage.setItem("snm.push.owner", "u-agronomist");
    server.use(
      http.post(
        "/api/push/subscriptions",
        () =>
          HttpResponse.json(
            { error: { code: "SERVICE_UNAVAILABLE", message: "The database can't be reached" } },
            { status: 503 },
          ),
        { once: true },
      ),
    );
    const user = userEvent.setup();
    renderAt("/alerts", { account: "u-agronomist" });
    expect(await screen.findByText(/Couldn’t save which alerts this device gets/)).toBeInTheDocument();
    expect(pushSubscriptions().size).toBe(0);

    await user.click(screen.getByRole("checkbox", { name: "Warning" }));
    await waitFor(() =>
      expect(pushSubscriptions().get(ENDPOINT)?.severities).toEqual(["CRITICAL", "WARNING"]),
    );
    expect(screen.queryByText(/Couldn’t save which alerts/)).not.toBeInTheDocument();
  });

  it("shows off on a shared browser where someone else turned it on, and tells the server nothing", async () => {
    registered = true;
    registration.subscription = new FakeSubscription(keyBytes(FAKE_PUSH_KEY).buffer);
    window.localStorage.setItem("snm.push.owner", "u-owner");
    renderAt("/alerts", { account: "u-agronomist" });
    const box = await appClosed();
    await waitFor(() => expect(box).toBeEnabled());
    expect(box).not.toBeChecked();
    expect(pushSubscriptions().size).toBe(0);
  });

  it("stops waiting and says so when the worker fails to install", async () => {
    const worker = {
      state: "installing",
      addEventListener: (_: string, listener: () => void) =>
        setTimeout(() => {
          worker.state = "redundant";
          listener();
        }),
    };
    Object.assign(registration, { installing: worker });
    const user = userEvent.setup();
    try {
      renderAt("/alerts", { account: "u-agronomist" });
      const box = await appClosed();
      await waitFor(() => expect(box).toBeEnabled());
      await user.click(box);
      expect(await screen.findByText(/couldn’t start notifications/)).toBeInTheDocument();
      expect(box).not.toBeChecked();
      expect(box).toHaveAccessibleDescription(/^Critical alerts/); // not "Asking the browser…" for ever
    } finally {
      Object.assign(registration, { installing: null });
    }
  });

  it("tells a Viewer that alerts go mostly to other roles", async () => {
    renderAt("/alerts", { account: "u-viewer" });
    const box = await appClosed();
    await waitFor(() => expect(box).toHaveAccessibleDescription(/a Viewer may get few or none/));
  });

  it("stops this browser's pushes before signing out", async () => {
    const user = userEvent.setup();
    renderAt("/alerts", { account: "u-agronomist" });
    const box = await appClosed();
    await waitFor(() => expect(box).toBeEnabled());
    await user.click(box);
    await waitFor(() => expect(pushSubscriptions().size).toBe(1));
    const subscription = registration.subscription!;

    await user.click(screen.getByRole("button", { name: "Sign out" }));
    await screen.findByRole("heading", { name: /^Sign in/ }); // the form, or demo mode's role picker
    expect(pushSubscriptions().size).toBe(0);
    expect(subscription.unsubscribe).toHaveBeenCalled();
  });

  it("opens the alert's zone when its notification is clicked with the app open", async () => {
    renderAt("/alerts", { account: "u-agronomist" });
    await screen.findByRole("heading", { level: 1, name: "Alerts" });
    await waitFor(() => expect(fromWorker).not.toBeNull());

    act(() =>
      fromWorker!(new MessageEvent("message", { data: { type: "OPEN_ALERT", page: "//elsewhere.test/" } })),
    );
    expect(screen.getByRole("heading", { level: 1, name: "Alerts" })).toBeInTheDocument(); // never another site

    act(() => fromWorker!(new MessageEvent("message", { data: { type: "OPEN_ALERT", page: "/zone/Z2" } })));
    expect(await screen.findByRole("heading", { level: 1, name: "Zone B" })).toBeInTheDocument();
  });
});
