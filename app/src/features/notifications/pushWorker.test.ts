import { beforeEach, describe, expect, it, vi } from "vitest";
import source from "../../../public/push/sw.js?raw";

/* public/push/sw.js, run in a fake service worker: what a push shows, and where a click goes. */

type Handler = (event: Record<string, unknown>) => void;

let handlers: Record<string, Handler>;
let shown: { title: string; options: NotificationOptions & { data: { page: string } } }[];
let windows: { url: string; focus: ReturnType<typeof vi.fn>; postMessage: ReturnType<typeof vi.fn> }[];
let openWindow: ReturnType<typeof vi.fn>;

/** Run one event through the worker and wait for what it asked to wait for. */
async function fire(type: string, event: Record<string, unknown>) {
  let waiting: Promise<unknown> = Promise.resolve();
  handlers[type]({ ...event, waitUntil: (p: Promise<unknown>) => (waiting = p) });
  await waiting;
}

const pushOf = (data: unknown) => ({
  data: { json: () => (typeof data === "string" ? JSON.parse(data) : data) },
});

beforeEach(() => {
  handlers = {};
  shown = [];
  windows = [];
  openWindow = vi.fn(async () => null);
  const self = {
    addEventListener: (type: string, handler: Handler) => (handlers[type] = handler),
    skipWaiting: () => {},
    location: { origin: "https://farm.example" },
    registration: {
      showNotification: async (title: string, options: (typeof shown)[number]["options"]) => {
        shown.push({ title, options });
      },
    },
    clients: { claim: async () => {}, matchAll: async () => windows, openWindow },
  };
  // the worker's code, with this fake as its `self`
  new Function("self", source)(self);
});

const leak = {
  alertId: "3c2e-leak",
  kind: "LEAK_DETECTED",
  severity: "CRITICAL",
  zone: "Z1",
  title: "Critical: Leak detected in Zone A",
  body: "Close the zone's inlet valve and check the bench",
  raisedAt: "2026-11-20T05:10:04.000Z",
};

describe("the push service worker", () => {
  it("shows a pushed alert as it came, tagged with its ID, and a critical one stays", async () => {
    await fire("push", pushOf(leak));
    expect(shown).toHaveLength(1);
    expect(shown[0].title).toBe("Critical: Leak detected in Zone A");
    expect(shown[0].options).toMatchObject({
      body: "Close the zone's inlet valve and check the bench",
      tag: "3c2e-leak",
      renotify: true,
      requireInteraction: true,
      data: { page: "/zone/Z1", alertId: "3c2e-leak" },
    });
  });

  it("lets a warning go by itself, and sends an alert of no zone to the Alerts page", async () => {
    await fire("push", pushOf({ ...leak, severity: "WARNING", zone: null }));
    expect(shown[0].options).toMatchObject({ requireInteraction: false, data: { page: "/alerts" } });
  });

  it("still shows something for a push it can't read", async () => {
    await fire("push", pushOf("not json {"));
    expect(shown[0].title).toBe("New alert");
  });

  it("opens the zone in a new window when the app is closed", async () => {
    const close = vi.fn();
    await fire("notificationclick", { notification: { close, data: { page: "/zone/Z1" } } });
    expect(close).toHaveBeenCalled();
    expect(openWindow).toHaveBeenCalledWith("/zone/Z1");
  });

  it("brings the open app to the front and has it open the zone", async () => {
    const app = { url: "https://farm.example/alerts", focus: vi.fn(async () => {}), postMessage: vi.fn() };
    windows = [{ url: "https://elsewhere.test/", focus: vi.fn(), postMessage: vi.fn() }, app];
    await fire("notificationclick", { notification: { close: vi.fn(), data: { page: "/zone/Z2" } } });
    expect(app.focus).toHaveBeenCalled();
    expect(app.postMessage).toHaveBeenCalledWith({ type: "OPEN_ALERT", page: "/zone/Z2" });
    expect(windows[0].postMessage).not.toHaveBeenCalled();
    expect(openWindow).not.toHaveBeenCalled();
  });
});
