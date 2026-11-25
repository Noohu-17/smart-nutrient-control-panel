/* The fake API's Web Push routes (contract §11.2, T1-41): the key, and which browsers asked for
   pushes, in the real routes' shapes and answers. It never pushes anything: there is no notifier
   behind it. On a site whose alerts.json has no WEB_PUSH in delivery, every route answers 404, as
   the real one does. */
import { HttpResponse } from "msw";
import type { ApiErrorBody, PushSeverity, PushSubscriptionAnswer } from "../api/types";
import alertsJson from "./data/alerts.json";
import { signedInIsDemo, signedInUser } from "./session";

/** A made-up P-256 public key (its private half was thrown away): enough for a browser to
    subscribe in development, never to push. */
export const FAKE_PUSH_KEY =
  "BErEqMZonJsRur4A4o28bPBS5_F-3W-osNq6IMfgOhbff7GVWQo-sGuaSeJxk4JLjWrx_FIGA3iMI5z4Q8W96Fc";

// the push services the real server sends to (backend/app/push/endpoints.py)
const PUSH_HOSTS = [
  /^fcm\.googleapis\.com$/,
  /\.push\.apple\.com$/,
  /\.push\.services\.mozilla\.com$/,
  /\.notify\.windows\.com$/,
];

/** endpoint → whose it is and what they asked for */
const subscriptions = new Map<string, { userId: string; severities: PushSeverity[] }>();

/** Tests: whether the site pushes, as its alerts.json delivery says (null: as the file says). */
let deliveryOverride: string[] | null = null;
export const setPushDelivery = (delivery: string[] | null) => (deliveryOverride = delivery);

export function resetPush() {
  subscriptions.clear();
  deliveryOverride = null;
}

/** What the fake server would push to: tests look here. */
export const pushSubscriptions = () => new Map(subscriptions);

const apiError = (status: number, code: string, message: string) =>
  HttpResponse.json({ error: { code, message } } satisfies ApiErrorBody, { status });

const pushes = () =>
  (deliveryOverride ?? (alertsJson as { delivery: string[] }).delivery).includes("WEB_PUSH");
const offHere = () => apiError(404, "NOT_FOUND", "This site doesn't send notifications");

const pushEndpoint = (endpoint: unknown) => {
  if (typeof endpoint !== "string") return false;
  try {
    const url = new URL(endpoint);
    return (
      url.protocol === "https:" &&
      !url.username &&
      !url.password &&
      PUSH_HOSTS.some((h) => h.test(url.hostname))
    );
  } catch {
    return false;
  }
};

export function pushKey() {
  if (!pushes()) return offHere();
  return HttpResponse.json({ publicKey: FAKE_PUSH_KEY });
}

export function addSubscription(body: {
  endpoint?: unknown;
  keys?: { p256dh?: unknown; auth?: unknown };
  severities?: unknown;
}) {
  if (!pushes()) return offHere();
  if (signedInIsDemo())
    return apiError(
      403,
      "FORBIDDEN",
      "Demo users can't turn on notifications: sign in with your own account",
    );
  if (
    !pushEndpoint(body.endpoint) ||
    typeof body.keys?.p256dh !== "string" ||
    typeof body.keys?.auth !== "string"
  )
    return apiError(
      422,
      "VALIDATION_ERROR",
      "endpoint must be a push service's https address, with keys p256dh and auth",
    );
  const asked = body.severities ?? ["CRITICAL"];
  if (
    !Array.isArray(asked) ||
    asked.length < 1 ||
    asked.length > 2 ||
    !asked.every((s) => s === "CRITICAL" || s === "WARNING")
  )
    return apiError(422, "VALIDATION_ERROR", "severities must be CRITICAL and/or WARNING");
  // critical always, whatever was asked (the server adds it too)
  const severities = [...new Set<PushSeverity>(["CRITICAL", ...(asked as PushSeverity[])])].sort();
  // sent again it changes them; from another user, the browser is theirs now
  subscriptions.set(body.endpoint as string, { userId: signedInUser()!.id, severities });
  return HttpResponse.json({
    endpoint: body.endpoint as string,
    severities,
  } satisfies PushSubscriptionAnswer);
}

export function removeSubscription(body: { endpoint?: unknown }) {
  if (!pushes()) return offHere();
  if (typeof body.endpoint !== "string") return apiError(422, "VALIDATION_ERROR", "endpoint is required");
  subscriptions.delete(body.endpoint);
  return new HttpResponse(null, { status: 204 });
}
