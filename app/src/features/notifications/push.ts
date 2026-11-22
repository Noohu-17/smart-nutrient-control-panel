// src/features/notifications/push.ts · Web Push in the app (after T1-41, contract §11.2): this
// browser gets alerts as notifications even with the app closed. The service worker that shows
// them is public/push/sw.js. Subscribing happens only from the Notifications setting, never on load.
import { ApiError, deleteJson, getJson, postJson } from "../../api/client";
import type { PushKey, PushSeverity, PushSubscriptionAnswer, PushSubscriptionBody } from "../../api/types";
import type { Severity } from "./notify";

/** The worker and its scope. Not "/": in development the fake API's worker has that scope. */
export const PUSH_WORKER = "/push/sw.js";
export const PUSH_SCOPE = "/push/";

export const pushSupported = () =>
  typeof navigator !== "undefined" &&
  "serviceWorker" in navigator &&
  typeof window !== "undefined" &&
  "PushManager" in window &&
  typeof Notification !== "undefined";

/** What this person picked in the app, as the server pushes it: CRITICAL always, WARNING if
    chosen, never INFO (only CRITICAL and WARNING are pushed, §11.2). */
export const pushSeverities = (chosen: Severity[]): PushSeverity[] =>
  chosen.includes("WARNING") ? ["CRITICAL", "WARNING"] : ["CRITICAL"];

/** The public key as the bytes PushManager.subscribe wants. */
export function keyBytes(base64url: string): Uint8Array<ArrayBuffer> {
  const base64 = base64url
    .replace(/-/g, "+")
    .replace(/_/g, "/")
    .padEnd(Math.ceil(base64url.length / 4) * 4, "=");
  const text = atob(base64);
  const bytes = new Uint8Array(new ArrayBuffer(text.length));
  for (let i = 0; i < text.length; i++) bytes[i] = text.charCodeAt(i);
  return bytes;
}

const sameKey = (a: ArrayBuffer | null, b: Uint8Array) => {
  if (!a || a.byteLength !== b.byteLength) return false;
  const x = new Uint8Array(a);
  return x.every((v, i) => v === b[i]);
};

/** The worker's registration once it is active (it controls no page, so `ready` never comes).
    Throws if the new worker fails to install (it becomes redundant), instead of waiting forever. */
async function activeRegistration(): Promise<ServiceWorkerRegistration> {
  const registration = await navigator.serviceWorker.register(PUSH_WORKER, { scope: PUSH_SCOPE });
  const coming = registration.installing ?? registration.waiting;
  if (coming && coming.state !== "activated")
    await new Promise<void>((resolve, reject) => {
      const settled = () => {
        if (coming.state === "activated") resolve();
        else if (coming.state === "redundant")
          reject(new PushProblem("The browser couldn’t start notifications. Reload the page and try again."));
      };
      coming.addEventListener("statechange", settled);
      settled();
    });
  return registration;
}

/** Who turned pushes on in this browser, so on a shared device the next person to sign in sees
    the switch off rather than someone else's "on". Kept in localStorage, beside the browser's
    subscription; the server is told only when that person turns it on themselves. */
const OWNER_KEY = "snm.push.owner";

export function pushOwner(): string | null {
  try {
    return window.localStorage.getItem(OWNER_KEY);
  } catch {
    return null;
  }
}

function setPushOwner(userId: string | null) {
  try {
    if (userId) window.localStorage.setItem(OWNER_KEY, userId);
    else window.localStorage.removeItem(OWNER_KEY);
  } catch {
    // private window or blocked storage: the switch then shows off on the next visit
  }
}

/** This browser's push subscription, if it has one. */
export async function currentSubscription(): Promise<PushSubscription | null> {
  if (!pushSupported()) return null;
  const registration = await navigator.serviceWorker.getRegistration(PUSH_SCOPE);
  return registration ? registration.pushManager.getSubscription() : null;
}

/** Turn pushes on for this browser and the signed-in person, with these severities. The browser
    asks for permission first if it hasn't yet. Throws a PushProblem the setting can show. */
export async function subscribe(severities: PushSeverity[], userId: string): Promise<PushSubscriptionAnswer> {
  if (!pushSupported()) throw new PushProblem("This browser can’t get notifications with the app closed.");
  const permission =
    Notification.permission === "default" ? await Notification.requestPermission() : Notification.permission;
  if (permission !== "granted")
    throw new PushProblem(
      "Notifications are blocked for this site. Allow them in the browser’s site settings.",
    );

  const { publicKey } = await getJson<PushKey>("/push/key");
  const key = keyBytes(publicKey);
  const registration = await activeRegistration();
  let subscription = await registration.pushManager.getSubscription();
  // made with another server key (the server's pair was changed): start again
  if (subscription && !sameKey(subscription.options.applicationServerKey, key)) {
    await subscription.unsubscribe();
    subscription = null;
  }
  subscription ??= await registration.pushManager.subscribe({
    userVisibleOnly: true,
    applicationServerKey: key,
  });

  const { endpoint, keys } = subscription.toJSON();
  try {
    const answer = await postJson<PushSubscriptionAnswer>("/push/subscriptions", {
      endpoint: endpoint!,
      keys: { p256dh: keys!.p256dh, auth: keys!.auth },
      severities,
    } satisfies PushSubscriptionBody);
    setPushOwner(userId);
    return answer;
  } catch (error) {
    // the server won't send to it, so the browser shouldn't keep it
    await subscription.unsubscribe().catch(() => {});
    throw error;
  }
}

/** Change which severities this browser is pushed: the same subscription, sent again (§11.2).
    A subscription made with an older server key (the server's pair was changed) gets no pushes, so
    with permission already granted it is made again with `publicKey`, without asking. */
export async function updateSeverities(severities: PushSeverity[], publicKey: string, userId: string) {
  const subscription = await currentSubscription();
  if (!subscription) return;
  if (!sameKey(subscription.options.applicationServerKey, keyBytes(publicKey))) {
    if (Notification.permission === "granted") await subscribe(severities, userId);
    return;
  }
  const { endpoint, keys } = subscription.toJSON();
  await postJson<PushSubscriptionAnswer>("/push/subscriptions", {
    endpoint: endpoint!,
    keys: { p256dh: keys!.p256dh, auth: keys!.auth },
    severities,
  } satisfies PushSubscriptionBody);
}

/** Turn pushes off for this browser: the server forgets it, and so does the browser. Used by the
    setting and before sign-out. Never throws: even if the server can't be reached, the browser
    drops the subscription, and the push service then tells the server it has gone. */
export async function unsubscribe() {
  setPushOwner(null);
  const subscription = await currentSubscription().catch(() => null);
  if (!subscription) return;
  await deleteJson("/push/subscriptions", { endpoint: subscription.endpoint }).catch(() => {});
  await subscription.unsubscribe().catch(() => {});
}

/** Something the person can be told, as it is. */
export class PushProblem extends Error {}

/** The words for a failed subscribe, from the server's answer where it has one. */
export function pushProblemText(error: unknown): string {
  if (error instanceof PushProblem) return error.message;
  if (error instanceof ApiError) {
    if (error.status === 404) return "This site doesn’t send notifications.";
    if (error.status === 503) return "Notifications aren’t set up on the server yet.";
    return error.message; // 403 for a demo user, 422 for an endpoint the server won't send to
  }
  if (error instanceof DOMException && error.name === "NotAllowedError")
    return "Notifications are blocked for this site. Allow them in the browser’s site settings.";
  return "Couldn’t turn on notifications. Try again.";
}
