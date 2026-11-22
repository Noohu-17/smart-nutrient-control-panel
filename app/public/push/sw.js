/* The app's service worker for Web Push (contract §11.2, T1-41): it shows each pushed alert, even
   with the app closed, and a click opens the alert's zone (or the Alerts page). It does nothing
   else: no caching, no offline pages.

   Its scope is /push/, not /, so it never takes the place of the fake API's worker in
   development (mockServiceWorker.js, scope /). It controls no page; pushes and clicks don't need it. */

/** The page an alert opens: its zone, or the Alerts page for one of no zone. As the app's zonePath. */
const pageFor = (alert) => (alert && alert.zone ? `/zone/${encodeURIComponent(alert.zone)}` : "/alerts");

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

// { alertId, kind, severity, zone, title, body, raisedAt }: title and body are shown as they are.
// The alert's ID is the tag, as the app's own browser notifications (T2-32): a repeat replaces it.
self.addEventListener("push", (event) => {
  let alert = null;
  try {
    alert = event.data ? event.data.json() : null;
  } catch {
    alert = null;
  }
  // every push must show something (userVisibleOnly), even one this worker can't read
  const title = alert && alert.title ? alert.title : "New alert";
  event.waitUntil(
    self.registration.showNotification(title, {
      body: alert && alert.body ? alert.body : "Open the app to see it.",
      tag: alert && alert.alertId ? alert.alertId : undefined,
      renotify: Boolean(alert && alert.alertId),
      // a critical alert stays on screen until someone looks at it
      requireInteraction: Boolean(alert && alert.severity === "CRITICAL"),
      icon: "/favicon.svg",
      data: { page: pageFor(alert), alertId: alert ? alert.alertId : null },
    }),
  );
});

// A click opens the alert: in the app's window if one is open (it navigates there itself), else in a new one.
self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const page = (event.notification.data && event.notification.data.page) || "/alerts";
  event.waitUntil(
    (async () => {
      const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
      const app = windows.find((w) => new URL(w.url).origin === self.location.origin);
      if (app) {
        // the page first, so it opens even if the browser won't bring the window forward
        app.postMessage({ type: "OPEN_ALERT", page });
        await app.focus().catch(() => {});
        return;
      }
      await self.clients.openWindow(page);
    })(),
  );
});
